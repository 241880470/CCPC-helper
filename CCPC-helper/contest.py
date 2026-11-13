# -*- coding: utf-8 -*-
"""组题模块：AI 从 CCPC 真题题库中挑选题目组成模拟卷。

优先使用 memory/problem_bank.json（由 build_problem_bank.py 从 Codeforces
gym 页面抓取，标题/题号/链接均真实），AI 只负责"选哪些题、为什么选"，
题库为空时提示导入，不自动切换为 AI 自由命题。
"""

import json
import os
import random

import config
from bank_manager import read_bank
from AI_agent import ask_gpt
from memory_manager import load_history

LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

_SYSTEM = (
    "你是 CCPC 校队教练，擅长根据队伍薄弱点安排针对性训练。"
    "你只能从给定题库中选题，绝不编造题目或 id，输出严格的 JSON。"
)


def load_bank():
    """读取并校验真实题库；缺失时返回空列表。"""
    if not os.path.exists(config.BANK_FILE):
        return []
    return read_bank(config.BANK_FILE)


def _recent_history_text(history, n=3):
    recent = history[-n:] if history else []
    if not recent:
        return "（暂无历史记录）"
    return json.dumps(recent, ensure_ascii=False, indent=2)


def _done_urls(history):
    """收集历史比赛中已出现过的题目链接。"""
    urls = set()
    for entry in history or []:
        if not isinstance(entry, dict):
            continue
        for p in entry.get("problems", []) or []:
            if isinstance(p, dict) and p.get("url"):
                urls.add(p["url"])
    return urls


def _bank_prompt(memory, bank, count, extra_note=""):
    def desc(p):
        line = f'{p["id"]} | {p["source"]} | {p["title"]}'
        if p.get("tags"):
            line += f' | 主题: {"、".join(p["tags"])}'
        if p.get("accepted_teams") is not None:
            line += f' | 当时AC队伍数: {p["accepted_teams"]}'
        return line

    bank_text = "\n".join(desc(p) for p in bank)
    weakness = memory.get("weakness", [])
    weakness_text = (
        json.dumps(weakness, ensure_ascii=False)
        if weakness
        else "（尚未记录，请均衡选题）"
    )
    return f"""请从下面的题库中为我校队伍挑选 {count} 道题，组成一场 CCPC 区域赛模拟卷。

队伍记忆（薄弱点，请重点针对）：
{json.dumps(memory, ensure_ascii=False, indent=2)}

最近几场训练复盘：
{_recent_history_text(load_history())}

题库（只能从这里面挑，id 是唯一标识）：
{bank_text}

要求：
1. 恰好选 {count} 道，id 必须一字不差地来自上面题库，不得编造。
2. 难度梯度贴近 CCPC 区域赛，**依据题库中"当时AC队伍数"这一客观数据**判断难度：
   约 2 道签到题（AC 队伍数 1000+）、5-6 道中等题（100~1000）、2-3 道较难题（少于 100），
   尽量让整套卷的难度分布合理，不要全选同一档。
3. 重点针对队伍薄弱点（{weakness_text}），主题覆盖尽量广（可参考题库中的"主题"字段）。
4. 每道题给出 reason：只从训练角度说明（如"练习字符串哈希、中档题"），
   不要编造题目的具体做法、难度标签或解题细节；不熟悉的题写"针对薄弱点综合训练"即可。
5. 同一场比赛最多选 2 题，避免整套卷来自同一场。
6. 题库中已尽量排除最近做过的题目，不要重复选最近几场训练做过的题。
{extra_note}
只返回 JSON，不要任何其他文字：
{{"contest_name": "...", "picked": [{{"bank_id": "103428A", "reason": "..."}}]}}"""


def _generate_from_bank(memory, bank, count, extra_done=None):
    """让 AI 从题库中选题（排除最近做过的题），再映射回真实题目信息。"""
    done = _done_urls(load_history())
    for u in extra_done or []:
        if u:
            done.add(u)
    candidates = [p for p in bank if p.get("url") not in done]
    note = ""
    if len(candidates) < count:
        note = (
            "注意：未做过的题目已不足，候选列表中已放回部分做过的题目，"
            "请优先选未做过的。"
        )
        candidates = bank
    result = ask_gpt(_bank_prompt(memory, candidates, count, note), system=_SYSTEM)
    if not isinstance(result, dict):
        raise ValueError("AI 返回格式不正确，请重试")

    picked = result.get("picked")
    if not isinstance(picked, list) or not picked:
        raise ValueError("AI 没有选出任何题目，请重试")

    lookup = {str(p.get("id")): p for p in bank}
    problems, used = [], set()
    for item in picked:
        if not isinstance(item, dict):
            continue
        bid = str(item.get("bank_id", "")).strip()
        if bid in used or bid not in lookup:
            continue
        p = lookup[bid]
        problems.append(
            {
                "letter": LETTERS[len(problems)],
                "title": p["title"],
                "source": p["source"],
                "url": p["url"],
                "bank_letter": p["letter"],
                "reason": str(item.get("reason", "")),
                "accepted_teams": p.get("accepted_teams"),
                "total_teams": p.get("total_teams"),
            }
        )
        used.add(bid)
        if len(problems) >= count:
            break

    if len(problems) < count:
        raise ValueError(
            f"AI 只选出了 {len(problems)} 道有效题（需要 {count} 道），请重新生成"
        )

    return {
        "contest_name": str(result.get("contest_name") or "CCPC 模拟赛"),
        "problems": problems,
        "from_bank": True,
    }


# 难度档累计比例：12 题时为 2 / 5 / 8 / 10 道题。
_DIFFICULTY_CUTS = (2 / 12, 5 / 12, 8 / 12, 10 / 12)


def _difficulty_cutoffs(n):
    """把 12 题的档位比例缩放到本场题量，返回升序的下标边界。"""
    return [max(1, min(n, int(n * c + 0.5))) for c in _DIFFICULTY_CUTS]


def _assign_difficulty(problems):
    """按原场**通过率**（AC 队伍数 / 参赛队伍数）相对排名标难度档（1=签到 … 5=困难）。

    用通过率而不是 AC 队伍数绝对值：AC 队伍数会随参赛规模膨胀，
    1000/3600 队（约 28%）与 1000/1300 队（约 77%）完全不是一个难度。
    完全没有统计数据的题**不标难度**，
    置为 None，避免按题目排列顺序瞎分档。
    """
    rated = []
    for p in problems:
        ac = p.get("accepted_teams")
        total = p.get("total_teams")
        if ac is None:
            p["difficulty"] = None
            continue
        rate = ac / total if isinstance(total, (int, float)) and total > 0 else None
        rated.append((rate, float(ac), p))

    # 通过率降序；只有 AC 队伍数的题排在已知通过率之后，按 AC 队伍数降序
    rated.sort(key=lambda t: (t[0] is None, -(t[0] or 0.0), -t[1]))

    cuts = _difficulty_cutoffs(len(rated))
    for i, (_, _ac, p) in enumerate(rated):
        level = 1
        for c in cuts:
            if i >= c:
                level += 1
        p["difficulty"] = min(level, 5)


def generate_contest(memory, count=None, extra_done=None):
    """根据队伍记忆生成一场模拟赛，返回包含 problems 的 dict。

    extra_done: 额外要排除的题目链接（如上一场尚未复盘的比赛题目）。
    题目顺序随机化：生成后打乱并重新编号 A、B、C……
    """
    count = int(count or config.DEFAULT_PROBLEM_COUNT)
    count = max(config.MIN_PROBLEM_COUNT, min(config.MAX_PROBLEM_COUNT, count))

    bank = load_bank()
    if bank and len(bank) < count:
        raise ValueError(f"题库只有 {len(bank)} 道题，无法生成 {count} 道题的模拟赛。")
    if bank:
        result = _generate_from_bank(memory, bank, count, extra_done)
    else:
        raise ValueError("题库为空或损坏，请先从界面导入有效题库。")

    # 打乱题目顺序，重新编号（避免每场顺序相同）
    random.shuffle(result["problems"])
    for i, p in enumerate(result["problems"]):
        p["letter"] = LETTERS[i]
    _assign_difficulty(result["problems"])
    return result


