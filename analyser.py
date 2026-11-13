# -*- coding: utf-8 -*-
"""赛后复盘：把做题结果交给 AI 分析，输出指导并写回记忆文件"""

import json

from AI_agent import ask_gpt
from memory_manager import record_contest, save_summary_markdown

_SYSTEM = (
    "你是 CCPC 校队教练，负责赛后复盘与训练规划，"
    "擅长从做题数据中诊断队伍薄弱点，输出严格的 JSON。"
)


def analyze_contest(memory, contest, results, notes="", submissions=None):
    """让 AI 对本场模拟赛复盘，返回分析结果 dict。"""
    problems = contest.get("problems", [])
    problem_desc = "\n".join(
        f"{p['letter']}. {p['title']}（{p['source']}）｜训练目的：{p['reason']}"
        for p in problems
    )
    result_desc = "\n".join(
        f"{p['letter']}：{results.get(p['letter'], '未填写')}" for p in problems
    )

    submit_desc = "（无）"
    if submissions:
        submit_desc = "\n".join(
            f"{s.get('time','')}｜提交 {s.get('problem','?')} 题：{s.get('result','')}"
            + (f"｜备注：{s.get('note')}" if s.get("note") else "")
            for s in submissions
        )

    prompt = f"""请为刚结束的模拟赛做复盘。

队伍长期记忆：
{json.dumps(memory, ensure_ascii=False, indent=2)}

本场题目与训练目的：
{problem_desc}

各题完成情况（AC=通过；尝试未过=有明确失败记录；未做=用户确认未做；待确认=信息不足）：
{result_desc}

本场提交记录（按时间顺序，反映每题尝试次数与做题节奏）：
{submit_desc}

队员备注：
{notes or "（无）"}

注意：待确认表示提交数据不足，不能推断为未做或能力不足；不得据此新增薄弱点。

注意：你没有题目题面，不得根据题名臆测题目的具体知识点或算法标签（如"二分答案""图论"等）。
弱点分析的 topic 只能基于：组题时的训练目的、提交记录与队员备注中的事实；
无法确定的知识点一律写"未记录"，不要编造。

请输出 JSON：
{{
 "summary": "本场总体评价（2-3 句）",
 "weaknesses": [
   {{"topic": "薄弱知识点", "detail": "具体表现（尽量引用题号）", "suggestion": "针对性训练建议"}}
 ],
 "strengths": [
   {{"topic": "优势点", "detail": "具体表现", "suggestion": "如何保持"}}
 ],
 "team_advice": "给全队下一阶段的训练建议",
 "next_focus": ["接下来优先训练的知识点"]
}}"""

    analysis = ask_gpt(prompt, system=_SYSTEM)
    if not isinstance(analysis, dict):
        analysis = {}
    return analysis


def do_full_analysis(memory, contest, results, notes="", submissions=None, date=None):
    """完整复盘流程：AI 分析 → 写入历史/记忆 → 输出复盘报告文件。

    date: 补录旧比赛时可指定历史记录时间，默认当前时间。
    返回 (分析结果, 报告路径)。
    """
    analysis = validate_analysis(analyze_contest(memory, contest, results, notes, submissions))
    path = save_summary_markdown(contest, results, analysis, notes, submissions)
    record_contest(contest, results, analysis, submissions, date=date)
    return analysis, path


def validate_analysis(analysis):
    """在写入长期记忆前校验模型输出，避免半成品被当作成功复盘。"""
    if not isinstance(analysis, dict) or not isinstance(analysis.get('summary'), str) or not analysis['summary'].strip():
        raise ValueError('AI 没有返回有效复盘，请重试；训练记录已保留。')
    for key in ('weaknesses', 'strengths'):
        items = analysis.get(key, [])
        if not isinstance(items, list):
            raise ValueError('AI 复盘列表格式错误，请重试。')
        for item in items:
            if not isinstance(item, dict) or any(not isinstance(item.get(k, ''), str) for k in ('topic', 'detail', 'suggestion')):
                raise ValueError('AI 复盘条目格式错误，请重试。')
    if not isinstance(analysis.get('team_advice', ''), str):
        raise ValueError('AI 训练建议格式错误，请重试。')
    if not isinstance(analysis.get('next_focus', []), list) or any(not isinstance(x, str) for x in analysis.get('next_focus', [])):
        raise ValueError('AI 下阶段重点格式错误，请重试。')
    return analysis
