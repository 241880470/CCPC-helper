"""校验并导入本地题库，不以 AI 生成数据补充题库。"""
import json
from pathlib import Path
import re
from storage import save_json


def validate_bank(data):
    problems = data.get('problems') if isinstance(data, dict) else data
    if not isinstance(problems, list) or not problems:
        raise ValueError('题库须为非空题目数组，或包含 problems 数组的 JSON 对象。')
    result, ids, urls = [], set(), set()
    for number, problem in enumerate(problems, 1):
        if not isinstance(problem, dict):
            raise ValueError(f'第 {number} 条题目不是对象。')
        p = dict(problem)
        if any(not isinstance(p.get(k), str) or not p[k].strip() for k in ('title', 'source', 'url')):
            raise ValueError(f'第 {number} 条题目缺少题名、来源或链接。')
        match = re.fullmatch(r'https?://(?:www\.)?codeforces\.com/(gym|contest)/(\d+)/problem/([A-Z][A-Z0-9]*)/?', p['url'].strip())
        if not match:
            raise ValueError(f'第 {number} 条题目的链接须为 Codeforces 单题链接。')
        kind, cid, letter = match.groups()
        if 'letter' in p and str(p['letter']) != letter:
            raise ValueError(f'第 {number} 条题号与链接不一致。')
        if 'gym_id' in p and str(p['gym_id']) != cid:
            raise ValueError(f'第 {number} 条比赛编号与链接不一致。')
        p.update(letter=letter, gym_id=int(cid), url=f'https://codeforces.com/{kind}/{cid}/problem/{letter}')
        p['id'] = str(p.get('id') or f'{cid}{letter}').strip()
        if p['id'] in ids or p['url'] in urls:
            raise ValueError(f'第 {number} 条题目 ID 或链接重复。')
        for key in ('accepted_teams', 'total_teams'):
            value = p.get(key)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f'第 {number} 条统计数据须为非负整数。')
        if p.get('total_teams') is not None and p.get('accepted_teams') is not None and p['accepted_teams'] > p['total_teams']:
            raise ValueError(f'第 {number} 条 AC 队数不能超过参赛队数。')
        if not isinstance(p.get('tags', []), list) or any(not isinstance(t, str) for t in p.get('tags', [])):
            raise ValueError(f'第 {number} 条 tags 须为字符串数组。')
        p.setdefault('tags', [])
        ids.add(p['id'])
        urls.add(p['url'])
        result.append(p)
    return result


def read_bank(path):
    path = Path(path)
    if path.stat().st_size > 25 * 1024 * 1024:
        raise ValueError('题库超过 25 MB，请拆分后导入。')
    try:
        return validate_bank(json.loads(path.read_text(encoding='utf-8-sig')))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('题库不是有效的 UTF-8 JSON 文件。') from exc


def import_bank(path, incoming, existing=None):
    incoming = validate_bank(incoming)
    existing = validate_bank(existing) if existing else []
    merged = {p['id']: p for p in (existing or [])}
    merged.update({p['id']: p for p in incoming})
    problems = validate_bank(list(merged.values()))
    save_json(path, {'count': len(problems), 'problems': problems})
    return problems
