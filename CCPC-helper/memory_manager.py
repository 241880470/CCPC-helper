# -*- coding: utf-8 -*-
"""队伍记忆管理：长期记忆(team_memory.json) + 历史记录(history.json) + 复盘报告输出"""

import json
import os
import re
from copy import deepcopy
from storage import save_json, read_json, atomic_text
from datetime import datetime

import config

_DEFAULTS = {
    "team_name": "",
    "members": [],
    "skills": {},
    "weakness": [],
    "strength": [],
}


def load_memory():
    """读取队伍长期记忆，缺失字段自动补齐。"""
    data = read_json(config.MEMORY_FILE, deepcopy(_DEFAULTS))
    if not isinstance(data, dict):
        raise ValueError("队伍记忆须为 JSON 对象，请从备份恢复。")
    result = deepcopy(_DEFAULTS)
    result.update(data)
    return result


def save_memory(data):
    save_json(config.MEMORY_FILE, data)


def update_memory(update):
    memory = load_memory()
    memory.update(update)
    save_memory(memory)


def load_history():
    """读取历次模拟赛记录。"""
    data = read_json(config.HISTORY_FILE, [])
    if not isinstance(data, list):
        raise ValueError("历史文件格式错误，请从备份恢复。")
    return data


def _save_history(history):
    save_json(config.HISTORY_FILE, history)


# ---------------- 当前赛卷存档（防止程序中途被关导致本场数据全丢） ----------------

def save_current_contest(contest):
    """生成赛卷时立即落盘，程序意外关闭后仍能恢复本场题目清单。"""
    save_json(config.CURRENT_CONTEST_FILE, contest)


def load_current_contest():
    """读取赛卷存档；缺失时返回 None，损坏时尝试上一份备份。"""
    data = read_json(config.CURRENT_CONTEST_FILE)
    if data is None:
        return None
    if isinstance(data, dict) and data.get("problems"):
        return data
    raise ValueError("训练存档没有有效题目，请从其他备份恢复。")


def clear_current_contest():
    """复盘完成后清除赛卷存档。"""
    for path in (config.CURRENT_CONTEST_FILE, config.CURRENT_CONTEST_FILE + '.bak'):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def _topic_of(item):
    if isinstance(item, dict):
        return item.get("topic", "")
    return str(item)


def merge_weaknesses(weaknesses):
    """把复盘得出的薄弱点合并进长期记忆（按主题去重、保留最新描述）。"""
    if not weaknesses:
        return
    memory = load_memory()
    old_list = memory.get("weakness", [])
    index = {_topic_of(w): i for i, w in enumerate(old_list) if _topic_of(w)}
    for item in weaknesses:
        topic = _topic_of(item)
        if not topic:
            continue
        if topic in index:
            old = old_list[index[topic]]
            if isinstance(old, dict) and isinstance(item, dict):
                old.update(item)
            else:
                old_list[index[topic]] = item
        else:
            index[topic] = len(old_list)
            old_list.append(item)
    memory["weakness"] = old_list
    save_memory(memory)


def record_contest(contest, results, analysis, submissions=None, date=None):
    """比赛结束：写入历史记录，并把薄弱点合并进长期记忆。

    date: 可指定历史记录时间（补录旧比赛时用），默认当前时间。
    """
    entry = {
        "date": date or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "contest": contest.get("contest_name", "未命名模拟赛"),
        "results": results,
        "submissions": submissions or [],
        "problems": [
            {
                "letter": p.get("letter", "?"),
                "title": p.get("title", ""),
                "url": p.get("url", ""),
            }
            for p in contest.get("problems", [])
        ],
        "summary": analysis.get("summary", ""),
        "next_focus": analysis.get("next_focus", []),
    }
    history = load_history()
    contest_id = contest.get("contest_id")
    if contest_id:
        entry["contest_id"] = contest_id
        history = [row for row in history if row.get("contest_id") != contest_id]
    history.append(entry)
    _save_history(history)

    merge_weaknesses(analysis.get("weaknesses", []))


def save_summary_markdown(contest, results, analysis, notes="", submissions=None):
    """把复盘指导写入 SUMMARY_DIR 下的 Markdown 文件，返回文件路径。"""
    os.makedirs(config.SUMMARY_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", contest.get("contest_name", "模拟赛"))[:80].strip(" .") or "模拟赛"
    suffix = contest.get("contest_id")
    suffix = suffix if isinstance(suffix, str) and re.fullmatch(r"[a-f0-9]{32}", suffix) else stamp
    path = os.path.join(config.SUMMARY_DIR, f"复盘_{name}_{suffix}.md")

    lines = [f"# 复盘报告：{name}", "", f"- 复盘时间：{datetime.now():%Y-%m-%d %H:%M:%S}", ""]
    lines.append("## 本场结果")
    for p in contest.get("problems", []):
        letter = p.get("letter", "?")
        status = results.get(letter, "未填写")
        lines.append(f"- **{letter}**. {p.get('title','')}（{p.get('source','')}）→ {status}")
    if notes:
        lines += ["", "## 队员备注", "", notes]

    if submissions:
        lines += ["", "## 本场提交记录"]
        for s in submissions:
            extra = f"（{s.get('note')}）" if s.get("note") else ""
            lines.append(
                f"- {s.get('time','')}｜{s.get('problem','')} 题：{s.get('result','')}{extra}"
            )

    lines += ["", "## AI 总体评价", "", analysis.get("summary", "")]

    weaknesses = analysis.get("weaknesses", [])
    if weaknesses:
        lines += ["", "## 暴露出的不足"]
        for i, w in enumerate(weaknesses, 1):
            if isinstance(w, dict):
                lines.append(f"{i}. **{w.get('topic','')}**：{w.get('detail','')}")
                if w.get("suggestion"):
                    lines.append(f"   - 建议：{w.get('suggestion')}")
            else:
                lines.append(f"{i}. {w}")

    strengths = analysis.get("strengths", [])
    if strengths:
        lines += ["", "## 队伍优势"]
        for i, s in enumerate(strengths, 1):
            if isinstance(s, dict):
                lines.append(f"{i}. **{s.get('topic','')}**：{s.get('detail','')}")
            else:
                lines.append(f"{i}. {s}")

    if analysis.get("team_advice"):
        lines += ["", "## 全队训练建议", "", analysis["team_advice"]]
    if analysis.get("next_focus"):
        lines += ["", "## 下阶段重点", ""]
        lines += [f"- {x}" for x in analysis["next_focus"]]

    atomic_text(path, "\n".join(lines))
    return path


# ---------------- 提交记录（本地自动存档，供复盘参考） ----------------

def load_submit_log():
    """读取当前比赛的本地提交记录。"""
    if not os.path.exists(config.SUBMIT_LOG_FILE):
        return {"contest_name": "", "started_at": "", "entries": []}
    try:
        with open(config.SUBMIT_LOG_FILE, "r", encoding="utf8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"contest_name": "", "started_at": "", "entries": []}
    if not isinstance(data, dict):
        return {"contest_name": "", "started_at": "", "entries": []}
    data.setdefault("contest_name", "")
    data.setdefault("started_at", "")
    data.setdefault("entries", [])
    return data


def save_submit_log(log):
    save_json(config.SUBMIT_LOG_FILE, log)


def new_submit_log(contest):
    """开始新比赛时初始化提交记录。"""
    log = {
        "contest_name": contest.get("contest_name", "模拟赛"),
        "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "entries": [],
    }
    save_submit_log(log)
    return log


def add_submission(log, problem, result, note=""):
    """追加一条提交记录并立即写入本地文件。"""
    log["entries"].append({
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "problem": problem,
        "result": result,
        "note": note,
    })
    save_submit_log(log)


def clear_submit_log():
    """复盘完成后清空提交记录，避免下次误读。"""
    if os.path.exists(config.SUBMIT_LOG_FILE):
        os.remove(config.SUBMIT_LOG_FILE)
