# -*- coding: utf-8 -*-
"""补录被意外中断的模拟赛。

适用场景：比赛中程序被关闭（或崩溃），本场题目清单只存在内存里 → 丢失，
导致 history.json / summaries / 队伍记忆都没有本场记录，下一场组题还会重复选题。

还原思路（全部基于磁盘上的真实数据，不编造）：
1. 赛卷 PDF（ccpc_roundN.pdf）里每一页是「题号 标题 + 题目链接」→ 还原本场题目清单；
2. problem_bank.json 按链接补上 来源 / 原题号 / 官方题解主题 / 当时的 AC 队伍数；
3. cf_submit.fetch_verdicts 走 CF 公开 API，按比赛时间窗重抓真实提交记录。

用法（项目目录下）：
    python recover_round.py --pdf ccpc_round1.pdf --start "2026-09-23 17:59:59" \\
        --notes "D 题多了一个 log 导致 TLE；H 题 getchar 与关闭同步的问题" \\
        --pass "B,G,I" --handle ndxzy

    --pass  队友通过、但本账号查不到提交明细的题号（按 AC 计入结果）
    加 --dry-run 只打印还原结果，不调用 AI、不写任何文件。
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import config  # noqa: E402

_PDF_HEADER = "CCPC 模拟训练卷 · 中文题面"

# CF 判定 -> 本地结果（与 cf_submit.VERDICT_MAP 保持一致的兜底表）
_FAILED = ("WA", "TLE", "RE", "MLE", "CE", "PE", "尝试未过",
           "IDLENESS_LIMIT_EXCEEDED", "被Hack")


def _parse_round_pdf(path):
    """解析赛卷 PDF，返回 (卷名, [(题号, 标题, 链接), ...])。"""
    from pypdf import PdfReader

    contest_name = ""
    problems = []
    for page in PdfReader(path).pages:
        lines = [l.strip() for l in (page.extract_text() or "").splitlines() if l.strip()]
        lines = [l for l in lines if l != _PDF_HEADER]
        if not lines:
            continue
        url = next((l for l in lines if "codeforces.com" in l), "")
        # 「A. 标题」行的正则：题号必须是单个字母
        head = next(
            (l for l in lines if re.match(r"^[A-Z]\.\s*\S", l)),
            "",
        )
        if not head:
            continue
        letter, title = head.split(".", 1)
        problems.append((letter.strip(), title.strip(), url))
        if not contest_name:
            # 卷名在题号行之前的那一行
            idx = lines.index(head)
            if idx > 0:
                contest_name = lines[idx - 1]
    return contest_name, problems


def _enrich(problems_raw, bank):
    """用题库补全题目信息（来源、原题号、主题、AC 队伍数）。"""
    by_url = {p.get("url"): p for p in bank if p.get("url")}
    out = []
    for letter, title, url in problems_raw:
        p = by_url.get(url)
        if p is None:  # 题库若已重建，退化为按 gym 题号匹配
            code = re.search(r"/problem/(\w+)", url or "")
            code = code.group(1) if code else ""
            p = next(
                (
                    b for b in bank
                    if code and str(b.get("letter")) == code
                    and str(b.get("gym_id")) in (url or "")
                ),
                None,
            )
        item = {
            "letter": letter,
            "title": (p or {}).get("title") or title,
            "url": url,
            "source": (p or {}).get("source", ""),
            "bank_letter": (p or {}).get("letter", ""),
            "accepted_teams": (p or {}).get("accepted_teams"),
            "total_teams": (p or {}).get("total_teams"),
            "reason": "补录：原训练目的未留存",
        }
        tags = (p or {}).get("tags") or []
        if tags:
            item["reason"] += "；官方题解主题（真实数据）：" + "、".join(tags)
        if p is None:
            item["reason"] += "；题库中未找到该题"
        out.append(item)
    return out


def _derive_results(problems, submissions):
    """与桌面界面共用判定：有 AC 即通过，无记录时待确认。"""
    from training_session import derive_results
    return derive_results({"problems": problems}, submissions)


def _since_from_log():
    """从本地提交记录里取本场开始时间。"""
    try:
        with open(config.SUBMIT_LOG_FILE, "r", encoding="utf8") as f:
            started = json.load(f).get("started_at", "")
        datetime.strptime(started, "%Y-%m-%d %H:%M:%S")
        return started
    except (OSError, json.JSONDecodeError, ValueError, AttributeError, TypeError):
        return ""


def _newest_pdf():
    pdfs = sorted(
        (f for f in os.listdir(BASE_DIR)
         if re.fullmatch(r"ccpc_round\d+\.pdf", f)),
        key=lambda f: os.path.getmtime(os.path.join(BASE_DIR, f)),
    )
    return pdfs[-1] if pdfs else ""


def main():
    ap = argparse.ArgumentParser(description="补录被意外中断的模拟赛")
    ap.add_argument("--pdf", help="赛卷 PDF（默认取最新的 ccpc_roundN.pdf）")
    ap.add_argument("--handle", default=config.CF_HANDLE, help="CF handle")
    ap.add_argument("--start", help="本场开始时间，如 2026-09-23 17:59:59")
    ap.add_argument("--minutes", type=int, default=config.CONTEST_DURATION_MINUTES,
                    help="比赛时长（分钟）")
    ap.add_argument("--notes", default="", help="队员备注（卡题经过等）")
    ap.add_argument("--pass-letters", dest="pass_letters", default="",
                    help="队友通过但本账号无提交明细的题号，逗号分隔，如 B,G,I")
    ap.add_argument("--contest-name", default="", help="覆盖卷名")
    ap.add_argument("--dry-run", action="store_true", help="只还原不写盘")
    args = ap.parse_args()

    pdf = args.pdf or _newest_pdf()
    if not pdf or not os.path.exists(pdf if os.path.isabs(pdf) else os.path.join(BASE_DIR, pdf)):
        print(f"[×] 找不到赛卷 PDF：{pdf or '(无)'}")
        return 1
    pdf = pdf if os.path.isabs(pdf) else os.path.join(BASE_DIR, pdf)

    started = args.start or _since_from_log()
    if not started:
        print("[×] 缺少本场开始时间：请用 --start \"YYYY-MM-DD HH:MM:SS\" 指定")
        return 1
    t0 = datetime.strptime(started, "%Y-%m-%d %H:%M:%S")
    t_end = t0 + timedelta(minutes=args.minutes)          # 正式比赛结束时刻
    grace = max(0, int(getattr(config, "VERDICT_GRACE_MINUTES", 0)))
    until = t_end + timedelta(minutes=grace)              # 抓取窗口（含宽限）

    # --- 1) 还原题目清单 ---
    contest_name, raw = _parse_round_pdf(pdf)
    if not raw:
        print("[×] 未能从 PDF 中解析出题目（格式与预期不符）")
        return 1
    print(f"[√] 从 {os.path.basename(pdf)} 还原 {len(raw)} 道题")

    bank = []
    if os.path.exists(config.BANK_FILE):
        with open(config.BANK_FILE, "r", encoding="utf8") as f:
            data = json.load(f)
        bank = data.get("problems", []) if isinstance(data, dict) else data
    problems = _enrich(raw, bank)
    sources = {p["source"] for p in problems if p["source"]}
    if len(sources) == 1:  # 整套卷子来自同一场比赛：补上真实赛事名
        short = sources.pop().split(",")[0].strip()
        contest_name = f"{contest_name}｜{short}" if contest_name else short
    if args.contest_name:
        contest_name = args.contest_name

    # 难度档：沿用组题时的同一套规则（按原场 AC 队伍数排名）
    from contest import _assign_difficulty
    _assign_difficulty(problems)

    print(f"[√] 卷名：{contest_name}")
    print(f"[√] 比赛时间窗：{t0} → {t_end}"
          f"（抓取额外宽限至 {until:%Y-%m-%d %H:%M:%S}）")
    for p in problems:
        ac = p.get("accepted_teams")
        print(f"    {p['letter']}. {p['title']:<16} 原题 {p['bank_letter'] or '?':<2} "
              f"AC {ac if ac is not None else '?':>5} 队 | 难度档 {p['difficulty']}")

    # --- 2) 重抓真实提交 ---
    import cf_submit

    verdicts = {}
    if args.handle:
        try:
            verdicts = cf_submit.fetch_verdicts(
                args.handle, problems, int(t0.timestamp()), int(until.timestamp())
            )
            print(f"[√] 从 CF 抓取到 {len(verdicts)} 题有提交（handle: {args.handle}）")
        except Exception as e:  # noqa: BLE001
            print(f"[!] 抓取提交失败（{e}），本次将不写提交记录")
    else:
        print("[!] 未提供 --handle，跳过提交抓取")

    # --- 3) 组装提交记录与结果 ---
    submissions = []
    for letter in sorted(verdicts):
        for result, ts in verdicts[letter]:
            t = datetime.fromtimestamp(ts)
            note = "CF 评测结果（自动抓取）"
            if t > t_end:
                note = (
                    "CF 评测结果（自动抓取·超出比赛时长 "
                    f"{int((t - t_end).total_seconds() // 60)} 分钟）"
                )
            submissions.append({
                "time": t.strftime("%Y-%m-%d %H:%M:%S"),
                "problem": letter,
                "result": result,
                "note": note,
            })
    submissions.sort(key=lambda e: (e["time"], e["problem"]))
    results = _derive_results(problems, submissions)

    extra = [x.strip().upper() for x in args.pass_letters.split(",") if x.strip()]
    for letter in extra:
        if letter in results:
            results[letter] = "AC"
            submissions.append({
                "time": "比赛期间",
                "problem": letter,
                "result": "AC",
                "note": "队友通过，本账号无提交明细",
            })

    print("[√] 本场结果：")
    for p in problems:
        print(f"    {p['letter']}. {p['title']:<16} → {results[p['letter']]}")

    notes = args.notes.strip()
    notes += (
        f"\n\n（本场为事后补录：程序在比赛途中被关闭，题目清单由赛卷 PDF 还原，"
        f"提交由 CF API 按时间窗 {t0:%H:%M}–{until:%H:%M} 重抓。）"
    )

    if args.dry_run:
        print("\n[dry-run] 未调用 AI、未写任何文件。")
        return 0

    # --- 4) 复盘并写入记忆 ---
    from analyser import do_full_analysis
    from memory_manager import (clear_current_contest, clear_submit_log,
                                load_memory)

    contest = {"contest_name": contest_name, "problems": problems, "from_bank": True}
    analysis, path = do_full_analysis(
        load_memory(), contest, results, notes, submissions,
        date=started,   # 历史记录按真实比赛时间归档
    )
    clear_submit_log()
    clear_current_contest()
    print(f"\n[√] 复盘完成：{path}")
    print(f"[√] 历史记录与队伍记忆已更新（共 {sum(1 for v in results.values() if v == 'AC')} AC）")
    print(f"    总体评价：{analysis.get('summary', '')}")
    weak = analysis.get("weaknesses") or []
    if weak:
        print("[√] 记录的薄弱点：" + "、".join(
            str(w.get("topic", w)) if isinstance(w, dict) else str(w) for w in weak
        ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
