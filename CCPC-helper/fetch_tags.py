# -*- coding: utf-8 -*-
"""为题库题目提取知识点标签并写入 memory/problem_bank.json。

说明：CF 官方 API 对 gym 题目返回的 tags 始终为空（gym 比赛无官方标签），
题面页面也不显示标签，因此这里改为**抓取题面 + AI 分析**提取知识点标签。

用法：
  python fetch_tags.py                 # 处理所有还没标签的题目
  python fetch_tags.py --limit 20      # 本次最多处理 20 道（可反复运行，断点续跑）
  python fetch_tags.py --force         # 强制重新提取全部题目标签
  python fetch_tags.py --gyms 102769,102822
"""

import json
import sys
import time

import config
import statement_pdf
from AI_agent import ask_gpt

_TAG_SYSTEM = "你是算法竞赛教练，只输出严格的 JSON。"

# 允许的标签白名单（主题级，不包含具体算法/数据结构）
_ALLOWED_TAGS = [
    "模拟", "枚举", "贪心", "字符串", "数学", "数论", "组合数学", "概率期望",
    "线性代数", "计算几何", "图论", "树", "动态规划", "数据结构", "构造",
    "博弈", "交互", "位运算", "二分", "排序", "搜索", "矩阵", "多项式",
]

_TAG_PROMPT = """下面是一道算法竞赛题的题面。请判断它属于哪些**问题领域**（注意：不是解法）。

只能从下面词表中挑选，最多 3 个，按主要程度排序：
模拟、枚举、贪心、字符串、数学、数论、组合数学、概率期望、线性代数、计算几何、
图论、树、动态规划、数据结构、构造、博弈、交互、位运算、二分、排序、搜索、矩阵、多项式

要求：
1. 只能选题面能直接支撑的领域（如"求两点间最短路径"→图论；"给定一个字符串"→字符串）。
2. **不要写具体算法或数据结构名**（如"线段树""KMP""哈希""二分答案"），
   同一道题可以用多种算法解决，题面无法确定解法。
3. 宁可少写也不猜，无法确定就不要写该标签。
4. 只输出 JSON：{{"tags": ["图论"], "evidence": "题面要求求两点间最短路径"}}

题面：
{statement}"""


def ai_tag_statement(statement):
    """让 AI 从题面提取主题标签，返回 (标签列表, 依据)。"""
    try:
        result = ask_gpt(
            _TAG_PROMPT.format(statement=statement[:8000]), system=_TAG_SYSTEM
        )
    except Exception:  # noqa: BLE001
        return [], ""
    if not isinstance(result, dict):
        return [], ""
    tags = result.get("tags")
    if not isinstance(tags, list):
        return [], ""
    # 白名单校验：只接受词表内的标签，杜绝臆造
    out = []
    for t in tags:
        t = str(t).strip()
        if t in _ALLOWED_TAGS and t not in out:
            out.append(t)
    evidence = str(result.get("evidence", "")).strip()
    return out[:3], evidence


def load_bank():
    with open(config.BANK_FILE, "r", encoding="utf8") as f:
        return json.load(f)


def save_bank(data):
    with open(config.BANK_FILE, "w", encoding="utf8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def main(force=False, only_gyms=None, limit=None):
    data = load_bank()
    problems = data.get("problems", []) if isinstance(data, dict) else data
    if not problems:
        print("题库为空，请先运行 build_problem_bank.py")
        return

    todo = [p for p in problems if force or not p.get("tags")]
    if only_gyms:
        todo = [p for p in todo if p.get("gym_id") in only_gyms]
    if limit:
        todo = todo[:limit]

    if not todo:
        print(f"所有题目都已有标签（共 {len(problems)} 题），无需处理。")
        print("如需强制重新提取，加 --force")
        return

    print(f"待处理 {len(todo)} 道题：先并发抓取题面，再逐题 AI 提取标签……")
    statements = statement_pdf._fetch_statements(todo)

    updated, skipped = 0, 0
    for i, p in enumerate(todo, 1):
        stmt = statements.get(p.get("letter", ""), "")
        if not stmt or stmt.startswith("[题面抓取失败"):
            print(f"[{i}/{len(todo)}] [跳过] {p.get('id')}（题面抓取失败）")
            skipped += 1
            continue
        tags, evidence = ai_tag_statement(stmt)
        if tags:
            p["tags"] = tags
            p["tag_evidence"] = evidence
            print(
                f"[{i}/{len(todo)}] {p.get('id')} {str(p.get('title', ''))[:26]}"
                f" -> {'、'.join(tags)}"
            )
            if evidence:
                print(f"        依据：{evidence}")
            updated += 1
        else:
            print(f"[{i}/{len(todo)}] {p.get('id')} 未提取到标签")
            skipped += 1
        # 逐题保存，支持中断后继续
        data["tags_updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_bank(data)

    print(f"\n共为 {updated} 道题写入标签（跳过 {skipped} 道），已保存到 {config.BANK_FILE}")
    remaining = sum(1 for p in problems if not p.get("tags"))
    if remaining:
        print(f"仍有 {remaining} 道题没有标签，再次运行本脚本可继续处理。")


if __name__ == "__main__":
    args = sys.argv[1:]
    force = "--force" in args
    only = None
    limit = None
    usage = "用法: python fetch_tags.py [--limit 20] [--force] [--gyms 102769,102822]"
    if "--gyms" in args:
        i = args.index("--gyms")
        try:
            only = [int(x) for x in args[i + 1].split(",")]
        except (ValueError, IndexError):
            print(usage)
            sys.exit(1)
    if "--limit" in args:
        i = args.index("--limit")
        try:
            limit = int(args[i + 1])
        except (ValueError, IndexError):
            print(usage)
            sys.exit(1)
    main(force, only, limit)
