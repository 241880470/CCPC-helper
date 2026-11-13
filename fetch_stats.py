# -*- coding: utf-8 -*-
"""抓取每道题的客观难度数据（AC 队伍数 / 尝试队伍数），写入 memory/problem_bank.json。

数据来自 CF 官方 API contest.standings（gym 比赛需要 API 认证）：
在 https://codeforces.com/settings/api 生成 key，然后填入 config.py 的
CF_API_KEY / CF_API_SECRET，或设置同名环境变量。

相比让 AI 猜难度，这是比赛现场的真实统计，可作为选题的客观依据。

用法：
  python fetch_stats.py                # 更新题库中所有比赛
  python fetch_stats.py --gyms 102769  # 只更新指定比赛
"""

import hashlib
import json
import random
import string
import sys
import time

import requests

import config

API_BASE = "https://codeforces.com/api"


def cf_api(method, params):
    """带签名调用 CF API。"""
    params = dict(params)
    params["apiKey"] = config.CF_API_KEY
    params["time"] = int(time.time())
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    sig_src = f"{rand}/{method}?{query}#{config.CF_API_SECRET}"
    sig = hashlib.sha512(sig_src.encode()).hexdigest()
    resp = requests.get(f"{API_BASE}/{method}?{query}&apiSig={rand}{sig}", timeout=30)
    return resp.json()


def load_bank():
    with open(config.BANK_FILE, "r", encoding="utf8") as f:
        return json.load(f)


def save_bank(data):
    with open(config.BANK_FILE, "w", encoding="utf8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def fetch_gym_stats(gym_id):
    """返回 {题号: (AC队伍数, 尝试队伍数, 参赛队伍数)}。"""
    data = cf_api("contest.standings", {
        "contestId": gym_id, "from": 1, "count": 10000, "showUnofficial": "true",
    })
    if data.get("status") != "OK":
        raise RuntimeError(data.get("comment", "API 返回错误"))

    result = data.get("result", {})
    problems = result.get("problems", [])
    rows = result.get("rows", [])
    # problemResults 与 problems 按顺序一一对应（无 problemIndex 字段）
    idx_list = [p.get("index", "") for p in problems]

    accepted = {idx: 0 for idx in idx_list}
    attempted = {idx: 0 for idx in idx_list}
    for row in rows:
        for j, pr in enumerate(row.get("problemResults", []) or []):
            if j >= len(idx_list):
                break
            idx = idx_list[j]
            points = pr.get("points", 0) or 0
            rejected = pr.get("rejectedAttemptCount", 0) or 0
            if points > 0:
                accepted[idx] += 1
                attempted[idx] += 1
            elif rejected > 0:
                attempted[idx] += 1
    return {idx: (accepted[idx], attempted[idx], len(rows)) for idx in accepted}


def main(only_gyms=None):
    if not config.CF_API_KEY or not config.CF_API_SECRET:
        print("缺少 CF API 凭据。")
        print("请在 https://codeforces.com/settings/api 生成 key，")
        print("填入 config.py 的 CF_API_KEY / CF_API_SECRET（或设同名环境变量）。")
        return

    data = load_bank()
    problems = data.get("problems", []) if isinstance(data, dict) else data
    if not problems:
        print("题库为空，请先运行 build_problem_bank.py")
        return

    gyms = sorted({p.get("gym_id") for p in problems if p.get("gym_id")})
    if only_gyms:
        gyms = [g for g in gyms if g in only_gyms]

    done, failed = 0, 0
    for gid in gyms:
        try:
            stats = fetch_gym_stats(gid)
        except Exception as e:  # noqa: BLE001
            print(f"[跳过] gym {gid}: {e}")
            failed += 1
            time.sleep(0.5)
            continue

        cnt = 0
        for p in problems:
            if p.get("gym_id") != gid:
                continue
            ac, att, teams = stats.get(p.get("letter"), (0, 0, 0))
            p["accepted_teams"] = ac
            p["attempted_teams"] = att
            p["total_teams"] = teams
            p["pass_rate"] = round(ac / att, 3) if att else 0.0
            cnt += 1
        teams = max((v[2] for v in stats.values()), default=0)
        print(f"[完成] gym {gid}: {cnt} 道题（参赛队伍 {teams} 支）")
        done += 1
        time.sleep(1)  # 避免触发限流

    data["stats_updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save_bank(data)
    print(f"\n已更新 {done} 场比赛的难度数据（失败 {failed} 场），写入 {config.BANK_FILE}")


if __name__ == "__main__":
    args = sys.argv[1:]
    only = None
    if "--gyms" in args:
        i = args.index("--gyms")
        try:
            only = [int(x) for x in args[i + 1].split(",")]
        except (ValueError, IndexError):
            print("用法: python fetch_stats.py [--gyms 102769,102822]")
            sys.exit(1)
    main(only)
