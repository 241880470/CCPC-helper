# -*- coding: utf-8 -*-
"""构建 CCPC 真题题库：从 Codeforces gym 页面抓取题目（题号/标题/链接），
写入 memory/problem_bank.json。以后组题时 AI 只从题库里挑题，保证信息真实。

用法：python build_problem_bank.py
可自行往 GYM_CANDIDATES 里增删 gym ID，脚本会自动识别名称里带 CCPC 的比赛。
"""

import json
import os
import re
import time
import urllib.request

import config

# 候选 gym ID（由 Codeforces gyms 列表页自动筛选出的 CCPC / China Collegiate 场次）
GYM_CANDIDATES = [
    101206, 102055, 102361, 102394, 102431, 102769, 102798, 102822,
    102823, 102832, 103409, 103415, 103428, 103447, 103492, 103860,
    103941, 103964, 104008, 104023, 104053, 104065, 104095, 104207,
    104354, 104396, 104651, 104768, 104787, 104791, 104813, 105158,
    105170, 105173, 105336, 105385, 105459, 105481, 105487, 105540,
    105632, 105924, 105930, 105941, 105977, 106030, 106184, 106534,
    106554, 106565, 106695, 106696,
]

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
TIMEOUT = 10


def fetch(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf8", "ignore")


def parse_gym(gym_id):
    """抓取 gym 主页，返回 (比赛名, [(字母, 标题), ...])；不是 CCPC 返回 None。"""
    html = fetch(f"https://codeforces.com/gym/{gym_id}")
    m = re.search(r"<title>(.*?)</title>", html, re.S)
    title = m.group(1).strip() if m else ""
    name = title.replace(" - Codeforces", "").strip()
    name = name.removeprefix("Dashboard - ").strip()

    if "CCPC" not in name and "China Collegiate" not in name:
        return None
    if "warmup" in name.lower():  # 热身赛不是正式区域赛题
        return None

    # 题目表格：id 单元格 + 标题链接（含 HTML 注释）
    pattern = rf'href="/gym/{gym_id}/problem/([A-Z0-9]+)"><!--\s*-->\s*([^<]+)<'
    problems = [(letter, text.strip()) for letter, text in re.findall(pattern, html)]
    return name, problems


def load_bank_file():
    """读取现有题库，返回题目列表。"""
    if not os.path.exists(config.BANK_FILE):
        return []
    try:
        with open(config.BANK_FILE, "r", encoding="utf8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    if isinstance(data, dict):
        problems = data.get("problems", [])
    else:
        problems = data
    return problems if isinstance(problems, list) else []


def main(mode="incremental", only_gyms=None):
    """构建/更新题库。

    mode:
      - "incremental"（默认）：只抓取尚未收录的 gym，新题合并进现有题库；
      - "rebuild"：清空现有题库，重新抓取全部候选。
    only_gyms: 指定 gym ID 列表时，强制重抓这些比赛（替换旧条目）。
    """
    problems = [] if mode == "rebuild" else load_bank_file()
    existing_gyms = {p.get("gym_id") for p in problems if p.get("gym_id")}

    if only_gyms is not None:
        candidates = list(only_gyms)
    else:
        candidates = [g for g in GYM_CANDIDATES if g not in existing_gyms]

    if not candidates:
        print("所有候选 gym 均已收录，没有需要抓取的比赛。")
        print(f"当前题库：{len(problems)} 道题，来自 {len(existing_gyms)} 场比赛")
        return

    ok_gyms = []
    for gym_id in candidates:
        try:
            parsed = parse_gym(gym_id)
        except Exception as e:  # noqa: BLE001
            print(f"[跳过] gym {gym_id}: {e}")
            time.sleep(0.2)
            continue
        if parsed is None:
            continue
        name, items = parsed
        if not items:
            print(f"[跳过] gym {gym_id} ({name}): 未解析到题目")
            continue
        # 指定重抓或重建时，替换该 gym 的旧条目；否则该 gym 不应已存在
        problems = [p for p in problems if p.get("gym_id") != gym_id]
        for letter, title in items:
            problems.append(
                {
                    "id": f"{gym_id}{letter}",
                    "gym_id": gym_id,
                    "letter": letter,
                    "title": title,
                    "source": name,
                    "url": f"https://codeforces.com/gym/{gym_id}/problem/{letter}",
                    "tags": [],
                }
            )
        ok_gyms.append(f"{gym_id} {name} ({len(items)} 题)")
        print(f"[收录] {name}: {len(items)} 题")
        time.sleep(0.3)

    if not ok_gyms:
        print("本轮没有抓到任何新题目，请检查网络（可能需要代理访问 codeforces.com）")
        return

    # 按 gym 排序保存，顺序稳定
    problems.sort(key=lambda p: (p.get("gym_id", 0), p.get("letter", "")))
    data = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(problems),
        "problems": problems,
    }
    os.makedirs(os.path.dirname(config.BANK_FILE), exist_ok=True)
    with open(config.BANK_FILE, "w", encoding="utf8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"\n完成：题库共 {len(problems)} 道题，来自 {len({p.get('gym_id') for p in problems})} 场比赛")
    print(f"本轮新增 {len(ok_gyms)} 场：")
    for line in ok_gyms:
        print("  " + line)


if __name__ == "__main__":
    import sys

    args = sys.argv[1:]
    mode = "rebuild" if "--rebuild" in args else "incremental"
    only = None
    if "--gyms" in args:
        i = args.index("--gyms")
        try:
            only = [int(x) for x in args[i + 1].split(",")]
        except (ValueError, IndexError):
            print("用法: python build_problem_bank.py [--rebuild] [--gyms 104065,104160]")
            sys.exit(1)
    main(mode, only)
