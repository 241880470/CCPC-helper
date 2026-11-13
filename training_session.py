"""训练状态、比赛时间窗及成绩合并；不依赖界面或网络。"""
from copy import deepcopy
from datetime import datetime
import math
import re
import time
from uuid import uuid4

STATUSES = ('自动', 'AC', '尝试未过', '未做', '待确认')
FAILURES = {'WA', 'TLE', 'RE', 'MLE', 'CE', 'PE', '尝试未过', '被Hack'}


def parse_handles(text):
    handles, seen = [], set()
    for part in re.split(r'[,，\s]+', text.strip()):
        if part and part.casefold() not in seen:
            handles.append(part)
            seen.add(part.casefold())
    if len(handles) > 10 or any(not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', h) for h in handles):
        raise ValueError('CF 账号请用逗号分隔，最多填写 10 个，不要填写邮箱或密码。')
    return handles


def derive_results(contest, entries, manual=None):
    manual = manual or {}
    results = {}
    for problem in contest.get('problems', []):
        letter = problem['letter']
        chosen = manual.get(letter, {}).get('status', '自动')
        if chosen in STATUSES and chosen != '自动':
            results[letter] = chosen
            continue
        verdicts = {e.get('result') for e in entries if e.get('problem') == letter}
        if 'AC' in verdicts:
            results[letter] = 'AC'
        elif verdicts & FAILURES:
            results[letter] = '尝试未过'
        else:
            results[letter] = '待确认'
    return results


class TrainingSession:
    def __init__(self, contest, minutes=300, handles=None):
        if not isinstance(contest, dict) or not isinstance(contest.get('problems'), list) or not contest['problems']:
            raise ValueError('训练存档没有有效题目。')
        letters = set()
        for p in contest['problems']:
            if not isinstance(p, dict) or not isinstance(p.get('letter'), str) or p['letter'] in letters:
                raise ValueError('训练存档题号缺失或重复。')
            for key in ('title', 'source', 'url'):
                if not isinstance(p.get(key), str) or not p[key].strip():
                    raise ValueError('训练存档题目信息不完整。')
            letters.add(p['letter'])
        self.contest = deepcopy({k: v for k, v in contest.items() if k != '_training'})
        for problem in self.contest['problems']:
            problem.setdefault('reason', '未记录')
        self.contest_id = uuid4().hex
        self.phase = 'prepared'
        self.minutes = int(minutes)
        if not 1 <= self.minutes <= 1440:
            raise ValueError('比赛时长须为 1～1440 分钟。')
        self.handles = list(handles or [])
        self.started_at = 0
        self.ended_at = 0
        self.notes = ''
        self.manual = {}
        self.entries = []
        self.pdf_path = ''
        self.analysis = None
        self.report_path = ''

    def start(self, now=None):
        if self.phase != 'prepared':
            raise ValueError('本场已开始，不能重复开赛。')
        self.started_at = time.time() if now is None else now
        self.phase = 'running'

    def seconds_left(self, now=None):
        if self.phase == 'prepared':
            return self.minutes * 60
        if self.phase != 'running':
            return 0
        now = time.time() if now is None else now
        return max(0, math.ceil(self.started_at + self.minutes * 60 - now))

    def refresh(self, now=None):
        if self.phase == 'running' and self.seconds_left(now) <= 0:
            self.phase = 'ended'
            self.ended_at = self.started_at + self.minutes * 60
            return True
        return False

    def finish(self, now=None):
        if self.phase != 'running':
            raise ValueError('本场没有正在运行的比赛。')
        self.ended_at = min(time.time() if now is None else now, self.started_at + self.minutes * 60)
        self.phase = 'ended'

    def verdict_window(self, grace_minutes=0, now=None):
        if not self.started_at:
            raise ValueError('本场开赛时间未知，请手动确认结果。')
        now = time.time() if now is None else now
        end = self.ended_at or self.started_at + self.minutes * 60
        return self.started_at, min(now, end + max(0, grace_minutes) * 60)

    def merge_verdicts(self, handle, verdicts, grace_minutes=0, now=None):
        since, until = self.verdict_window(grace_minutes, now)
        letters = {p['letter'] for p in self.contest['problems']}
        fresh = []
        for letter, submissions in verdicts.items():
            if letter not in letters:
                continue
            for result, ts in submissions:
                if not isinstance(ts, (int, float)) or not since <= ts <= until:
                    continue
                end = self.ended_at or self.started_at + self.minutes * 60
                note = 'CF 评测结果（自动抓取）'
                if ts > end:
                    note += f'；超出正式时间 {math.ceil((ts-end)/60)} 分钟'
                fresh.append({'problem': letter, 'result': str(result), 'timestamp': ts,
                              'time': datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S'),
                              'handle': handle, 'source': 'cf', 'note': note})
        # 替换该账号快照，不抹掉其他账号或人工记录；重复抓取不追加副本。
        self.entries = [e for e in self.entries if not (e.get('source') == 'cf' and e.get('handle') == handle)]
        self.entries.extend(fresh)
        self.entries.sort(key=lambda e: e.get('timestamp', 0))

    def results(self):
        return derive_results(self.contest, self.entries, self.manual)

    def review_notes(self):
        lines = [self.notes] if self.notes.strip() else []
        for p in self.contest['problems']:
            detail = self.manual.get(p['letter'], {})
            elapsed = detail.get('minutes', '').strip()
            if elapsed:
                try:
                    value = float(elapsed)
                except ValueError as exc:
                    raise ValueError(f"{p['letter']} 题耗时须为非负数字，或留空。") from exc
                if not math.isfinite(value) or value < 0:
                    raise ValueError(f"{p['letter']} 题耗时须为非负数字，或留空。")
            lines.append(f"{p['letter']} 题：负责队员={detail.get('member') or '未记录'}；"
                         f"耗时={elapsed+' 分钟' if elapsed else '未记录'}；"
                         f"结果来源={'人工确认' if detail.get('status', '自动') != '自动' else '提交记录推导'}；"
                         f"备注={detail.get('note') or '无'}")
        return '\n'.join(lines)

    def to_dict(self):
        data = deepcopy(self.contest)
        data['contest_id'] = self.contest_id
        data['started_at'] = datetime.fromtimestamp(self.started_at).strftime('%Y-%m-%d %H:%M:%S') if self.started_at else ''
        data['_training'] = {key: deepcopy(getattr(self, key)) for key in (
            'phase', 'minutes', 'handles', 'started_at', 'ended_at', 'notes', 'manual', 'entries',
            'pdf_path', 'analysis', 'report_path')}
        data['_training']['version'] = 1
        return data

    @classmethod
    def from_dict(cls, data, default_minutes=300, legacy_entries=None, now=None):
        state = data.get('_training') if isinstance(data, dict) else None
        if state is not None and (not isinstance(state, dict) or state.get('version') != 1):
            raise ValueError('无法识别训练存档版本。')
        instance = cls(data, (state or {}).get('minutes', default_minutes))
        instance.contest_id = str(data.get('contest_id') or instance.contest_id)
        # 只把本地生成的十六进制 ID 用于存档文件名。
        if not re.fullmatch(r'[a-f0-9]{32}', instance.contest_id):
            instance.contest_id = uuid4().hex
        if state:
            for key in ('phase', 'handles', 'started_at', 'ended_at', 'notes', 'manual', 'entries', 'pdf_path', 'analysis', 'report_path'):
                if key in state:
                    setattr(instance, key, deepcopy(state[key]))
            if instance.phase not in ('prepared', 'running', 'ended', 'reviewed'):
                raise ValueError('训练状态无效。')
            if (not isinstance(instance.notes, str) or not isinstance(instance.manual, dict)
                    or not isinstance(instance.entries, list) or any(not isinstance(e, dict) for e in instance.entries)
                    or not isinstance(instance.handles, list) or any(not isinstance(h, str) for h in instance.handles)
                    or not isinstance(instance.started_at, (int, float)) or not math.isfinite(instance.started_at)
                    or not isinstance(instance.ended_at, (int, float)) or not math.isfinite(instance.ended_at)):
                raise ValueError('训练存档字段格式错误。')
            for detail in instance.manual.values():
                if (not isinstance(detail, dict) or any(not isinstance(v, str) for v in detail.values())
                        or detail.get('status', '自动') not in STATUSES):
                    raise ValueError('逐题记录格式错误。')
            if any(not isinstance(getattr(instance, key), str) for key in ('pdf_path', 'report_path')):
                raise ValueError('训练报告路径格式错误。')
            if instance.analysis is not None and not isinstance(instance.analysis, dict):
                raise ValueError('训练复盘格式错误。')
            for entry in instance.entries:
                if not isinstance(entry.get('timestamp', 0), (int, float)):
                    raise ValueError('训练提交时间格式错误。')
            if instance.phase == 'running' and instance.started_at <= 0:
                raise ValueError('比赛缺少有效开赛时间。')
        else:
            try:
                instance.started_at = datetime.strptime(data.get('started_at', ''), '%Y-%m-%d %H:%M:%S').timestamp()
            except (ValueError, TypeError):
                instance.started_at = 0
            instance.phase = 'running' if instance.started_at else 'ended'
            instance.entries = deepcopy(legacy_entries or [])
        instance.refresh(now)
        return instance
