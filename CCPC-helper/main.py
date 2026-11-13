# -*- coding: utf-8 -*-
"""CCPC 桌面训练助手：准备 → 开赛 → 记录 → 确认 → 复盘。"""
from collections import Counter
from copy import deepcopy
from datetime import datetime
import importlib.util
import json
import shutil
from pathlib import Path
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import webbrowser

import config
import cf_submit
import statement_pdf
from analyser import analyze_contest, validate_analysis
from background_tasks import BackgroundTasks
from bank_manager import read_bank, import_bank
from contest import generate_contest
from memory_manager import (load_current_contest, load_memory, load_submit_log,
                            record_contest, save_current_contest, save_submit_log, save_summary_markdown)
from settings_dialog import needs_setup, show_settings
from storage import save_json
from training_session import STATUSES, TrainingSession, derive_results, parse_handles

PHASE_NAMES = {'prepared': '赛前准备', 'running': '比赛进行中', 'ended': '比赛已结束', 'reviewed': '已完成复盘'}
DIFFICULTIES = {1: '签到题', 2: '简单题', 3: '中等题', 4: '较难题', 5: '困难题'}


class TrainingApp:
    def __init__(self, root, startup=True):
        self.root = root
        root.title('CCPC 模拟训练助手')
        root.geometry('1120x850')
        root.minsize(960, 680)
        self.session = None
        self.tasks = BackgroundTasks()
        self.task_success = None
        self.retry = None
        self.dirty = False
        self.save_after = None
        self.closed = False
        self.loading = False
        self.fields = {}
        self.editors = []
        self.difficulties = {}
        self._build_ui()
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(80, self._poll_tasks)
        root.after(500, self._tick)
        root.after(3000, self._autosave)
        if startup:
            root.after(200, self._startup)

    def _build_ui(self):
        self.status = tk.StringVar(value='先导入题库，再生成模拟赛；生成后不会立即计时。')
        self.save_status = tk.StringVar(value='尚无训练记录')
        self.task_status = tk.StringVar(value='')
        self.phase = tk.StringVar(value='尚未组卷')
        self.timer = tk.StringVar(value='未开始计时')
        self.bank_status = tk.StringVar(value='题库：未检查')
        self.count = tk.StringVar(value=str(config.DEFAULT_PROBLEM_COUNT))
        self.minutes = tk.StringVar(value=str(config.CONTEST_DURATION_MINUTES))
        self.handles = tk.StringVar(value=config.CF_HANDLE)
        self.menu = tk.Menu(self.root, tearoff=False)
        for label, command in [('设置', self.open_settings), ('CF 登录', self.login_cf),
                               ('导入题库', self.import_bank_dialog), ('恢复训练存档', self.restore_file),
                               ('查看队伍记忆', self.show_memory)]:
            self.menu.add_command(label=label, command=command)
        self.root.configure(menu=self.menu)
        prep = ttk.LabelFrame(self.root, text='赛前准备', padding=10)
        prep.pack(fill='x', padx=12, pady=(10, 5))
        ttk.Label(prep, text='题目数').grid(row=0, column=0)
        self.count_input = ttk.Spinbox(prep, from_=config.MIN_PROBLEM_COUNT, to=config.MAX_PROBLEM_COUNT,
                                       textvariable=self.count, width=5)
        self.count_input.grid(row=0, column=1, padx=(5, 15))
        ttk.Label(prep, text='时长（分钟）').grid(row=0, column=2)
        self.minutes_input = ttk.Spinbox(prep, from_=1, to=1440, textvariable=self.minutes, width=7)
        self.minutes_input.grid(row=0, column=3, padx=(5, 15))
        ttk.Label(prep, text='CF 账号（多人用逗号分隔，可留空）').grid(row=0, column=4)
        self.handles_input = ttk.Entry(prep, textvariable=self.handles)
        self.handles_input.grid(row=0, column=5, sticky='ew', padx=(5, 0))
        prep.columnconfigure(5, weight=1)
        ttk.Label(prep, textvariable=self.bank_status, foreground='#666').grid(
            row=1, column=0, columnspan=6, sticky='w', pady=(8, 0))
        self.minutes.trace_add('write', self._prep_changed)
        self.handles.trace_add('write', self._prep_changed)
        actions = ttk.Frame(self.root, padding=(12, 5))
        actions.pack(fill='x')
        self.buttons = {}
        for key, label, command in [
            ('generate', '生成模拟赛', self.generate), ('pdf', '生成中文题面 PDF', self.make_pdf),
            ('start', '开始比赛', self.begin), ('finish', '提前结束', self.finish),
            ('fetch', '抓取评测结果', self.fetch), ('review', '确认结果并复盘', self.review),
            ('report', '查看报告', self.show_report)]:
            button = ttk.Button(actions, text=label, command=command)
            button.pack(side='left', padx=(0, 5))
            self.buttons[key] = button
        phasebar = ttk.Frame(self.root, padding=(12, 5))
        phasebar.pack(fill='x')
        ttk.Label(phasebar, textvariable=self.phase, font=('Microsoft YaHei UI', 11, 'bold')).pack(side='left')
        ttk.Label(phasebar, textvariable=self.timer, foreground='#b42318', font=('Microsoft YaHei UI', 12, 'bold')).pack(side='right')
        area = ttk.Frame(self.root)
        area.pack(fill='both', expand=True, padx=12)
        self.canvas = tk.Canvas(area, highlightthickness=0)
        scroll = ttk.Scrollbar(area, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.cards = ttk.Frame(self.canvas)
        self.card_window = self.canvas.create_window((0, 0), window=self.cards, anchor='nw')
        self.cards.bind('<Configure>', lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(self.card_window, width=e.width))
        self.canvas.bind('<Enter>', lambda _e: self.canvas.bind_all('<MouseWheel>', self._scroll))
        self.canvas.bind('<Leave>', lambda _e: self.canvas.unbind_all('<MouseWheel>'))
        self.canvas.bind('<Button-4>', lambda _e: self.canvas.yview_scroll(-1, 'units'))
        self.canvas.bind('<Button-5>', lambda _e: self.canvas.yview_scroll(1, 'units'))
        bottom = ttk.Frame(self.root, padding=(12, 8))
        bottom.pack(fill='x')
        ttk.Label(bottom, text='队员备注（自动保存；逐题备注可填写在题目卡片中）').pack(anchor='w')
        self.notes = tk.Text(bottom, height=3, wrap='word')
        self.notes.pack(fill='x', pady=4)
        self.notes.bind('<<Modified>>', self._notes_changed)
        taskbar = ttk.Frame(bottom)
        taskbar.pack(fill='x')
        self.progress = ttk.Progressbar(taskbar, mode='indeterminate', length=100)
        self.progress.pack(side='left', padx=(0, 8))
        ttk.Label(taskbar, textvariable=self.task_status).pack(side='left', fill='x', expand=True)
        self.retry_button = ttk.Button(taskbar, text='重试上次任务', command=self.retry_task, state='disabled')
        self.retry_button.pack(side='right')
        ttk.Label(bottom, textvariable=self.status, wraplength=1060, foreground='#555').pack(anchor='w', pady=(5, 2))
        ttk.Label(bottom, textvariable=self.save_status, foreground='#666').pack(anchor='w')
        self._controls()

    def _scroll(self, event):
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')

    def _prep_values(self):
        try:
            minutes = int(self.minutes.get())
        except ValueError as exc:
            raise ValueError('时长须为 1～1440 的整数分钟。') from exc
        if not 1 <= minutes <= 1440:
            raise ValueError('时长须为 1～1440 的整数分钟。')
        return minutes, parse_handles(self.handles.get())

    def _prep_changed(self, *_args):
        if self.loading or not self.session or self.session.phase != 'prepared':
            return
        try:
            self.session.minutes, self.session.handles = self._prep_values()
        except ValueError:
            return
        self._mark_dirty()

    def _notes_changed(self, _event=None):
        if self.notes.edit_modified():
            self.notes.edit_modified(False)
            if not self.loading and self.session:
                self.session.notes = self.notes.get('1.0', 'end-1c')
                self._mark_dirty()

    def _field_changed(self, letter):
        if self.loading or not self.session:
            return
        self.session.manual[letter] = {key: var.get() for key, var in self.fields[letter].items() if key != 'auto'}
        self._mark_dirty()

    def _mark_dirty(self):
        if self.session and self.session.phase != 'reviewed':
            self.session.analysis = None
        self.retry = None
        self.retry_button.configure(state='disabled')
        self.dirty = True
        self.save_status.set('有改动，正在自动保存……')
        if self.save_after is not None:
            self.root.after_cancel(self.save_after)
        self.save_after = self.root.after(350, self._debounced_save)

    def _debounced_save(self):
        self.save_after = None
        self.save()

    def save(self, notify=False):
        if not self.session:
            return True
        self.session.notes = self.notes.get('1.0', 'end-1c')
        try:
            save_current_contest(self.session.to_dict())

        except (OSError, ValueError) as exc:
            self.dirty = True
            self.save_status.set('自动保存失败，请检查目录权限或磁盘空间；请勿关闭程序。')
            if notify:
                messagebox.showerror('保存失败', str(exc), parent=self.root)
            return False
        self.dirty = False
        suffix = ''
        try:
            save_submit_log({'contest_id': self.session.contest_id,
                             'contest_name': self.session.contest.get('contest_name', ''),
                             'started_at': self.session.to_dict()['started_at'],
                             'entries': self.session.entries})
        except (OSError, ValueError):
            suffix = ' · 兼容提交日志写入失败，主存档已保存'
        self.save_status.set('已保存 ' + datetime.now().strftime('%H:%M:%S') + ' · 进行中的比赛关闭后仍计时' + suffix)
        return True

    def _autosave(self):
        if self.closed:
            return
        if self.dirty:
            self.save()
        self.root.after(3000, self._autosave)

    def _controls(self):
        busy = self.tasks.busy
        phase = self.session.phase if self.session else None
        available = {
            'generate': not busy, 'pdf': bool(self.session) and not busy,
            'start': phase == 'prepared' and not busy, 'finish': phase == 'running' and not busy,
            'fetch': phase in ('running', 'ended') and not busy,
            'review': phase in ('running', 'ended') and not busy,
            'report': bool(self.session and self.session.report_path) and not busy}
        for key, button in self.buttons.items():
            button.configure(state='normal' if available[key] else 'disabled')
        locked = busy or phase in ('running', 'ended', 'reviewed')
        self.minutes_input.configure(state='disabled' if locked else 'normal')
        self.handles_input.configure(state='disabled' if locked else 'normal')
        self.count_input.configure(state='disabled' if busy else 'normal')
        for index in range(5):
            self.menu.entryconfigure(index, state='disabled' if busy else 'normal')
        self.retry_button.configure(state='normal' if self.retry and not busy else 'disabled')
        record_locked = busy or phase == 'reviewed'
        self.notes.configure(state='disabled' if record_locked else 'normal')
        for widget in self.editors:
            widget.configure(state='disabled' if record_locked else
                             'readonly' if isinstance(widget, ttk.Combobox) else 'normal')

    def _run(self, title, work, success):
        if not self.tasks.start(work):
            return False
        self.task_success = success
        self.retry = (title, work, success)
        self.task_status.set(title + '……')
        self.progress.start(12)
        self._controls()
        return True

    def _poll_tasks(self):
        if self.closed:
            return
        for kind, value in self.tasks.poll():
            if kind == 'progress':
                self.task_status.set(str(value))
                continue
            self.progress.stop()
            if kind == 'error':
                self.task_status.set('任务失败，可点击重试；训练记录仍保留')
                text = str(value)
                for key in ('API_KEY', 'CF_PASSWORD', 'CF_API_KEY', 'CF_API_SECRET'):
                    secret = getattr(config, key, '')
                    if secret:
                        text = text.replace(secret, '***')
                messagebox.showerror('任务失败', text, parent=self.root)
            else:
                self.retry = None
                self.task_status.set('任务完成')
                try:
                    self.task_success(value)
                except Exception as exc:
                    self.status.set('结果未能完整保存，请检查后重试；当前训练未清除。')
                    messagebox.showerror('保存或处理失败', str(exc), parent=self.root)
            self._controls()
        self.root.after(80, self._poll_tasks)

    def retry_task(self):
        if self.retry and not self.tasks.busy:
            self._run(*self.retry)

    def _require_ai(self):
        if needs_setup():
            self.open_settings()
        return not needs_setup()

    def open_settings(self):
        if self.tasks.busy:
            return
        if show_settings(self.root, first_run=needs_setup()):
            self.retry = None
            self._controls()
            if not self.session:
                self.handles.set(config.CF_HANDLE)
            self.status.set('连接配置已保存并生效。')

    def refresh_bank(self):
        try:
            problems = read_bank(config.BANK_FILE)
        except (OSError, ValueError) as exc:
            self.bank_status.set('题库不可用：请从上方「导入题库」选择有效 JSON 文件')
            return None
        tagged = sum(bool(p.get('tags')) for p in problems)
        stats = sum(p.get('accepted_teams') is not None for p in problems)
        self.bank_status.set(f'题库：{len(problems)} 题 · {len({p["source"] for p in problems})} 场来源 · 有标签 {tagged} 题 · 有难度统计 {stats} 题')
        return problems

    def import_bank_dialog(self):
        if self.tasks.busy:
            return
        path = filedialog.askopenfilename(parent=self.root, title='导入题库 JSON', filetypes=[('JSON', '*.json')])
        if not path:
            return
        def imported(incoming):
            try:
                existing = read_bank(config.BANK_FILE)
            except (OSError, ValueError):
                existing = []
            if existing:
                choice = messagebox.askyesnocancel('导入题库', f'文件包含 {len(incoming)} 题，现有 {len(existing)} 题。\n\n是：合并（相同 ID 更新）\n否：替换现有题库\n取消：不导入\n\n只做结构校验，题目是否可访问仍以 CF 为准。', parent=self.root)
                if choice is None:
                    return
            else:
                choice = False
                if not messagebox.askyesno('导入题库', f'已校验 {len(incoming)} 道题，确认导入？\n仅验证格式，未联网核验访问权限。', parent=self.root):
                    return
            import_bank(config.BANK_FILE, incoming, existing if choice else [])
            self.refresh_bank()
            self.status.set('题库导入成功，原题库的上一份可读版本保存在 .bak 文件中。')
        self._run('正在读取并校验题库', lambda _p: read_bank(path), imported)

    def generate(self):
        if self.tasks.busy:
            return
        try:
            minutes, handles = self._prep_values()
            count = int(self.count.get())
            if not config.MIN_PROBLEM_COUNT <= count <= config.MAX_PROBLEM_COUNT:
                raise ValueError('题目数须为 10～12。')
            bank = self.refresh_bank()
            if not bank:
                messagebox.showinfo('请先准备题库', '请通过上方「导入题库」导入真实题库，再生成模拟赛。', parent=self.root)
                return
            if len(bank) < count:
                raise ValueError(f'题库仅有 {len(bank)} 题，不足 {count} 题。')
            memory = load_memory()
        except (ValueError, OSError) as exc:
            messagebox.showerror('无法组卷', str(exc), parent=self.root)
            return
        if not self._require_ai():
            return
        if self.session and self.session.phase != 'reviewed':
            if not messagebox.askyesno('生成新赛卷', '当前训练尚未复盘。生成成功后将切换到新赛卷，当前训练会保留为存档，是否继续？', parent=self.root):
                return
        if not self.save(notify=True):
            return
        previous = {p['url'] for p in self.session.contest['problems']} if self.session else set()
        def generated(contest):
            session = TrainingSession(contest, minutes, handles)
            # 先持久化新赛卷；写入失败仍保留旧界面和旧存档。
            self._archive()
            save_current_contest(session.to_dict())
            self._load_session(session)
            self.status.set('赛卷已就绪。可先生成中文题面 PDF，准备好后点击「开始比赛」。')
        self._run('AI 正在组卷', lambda _p: generate_contest(memory, count, previous), generated)

    def make_pdf(self):
        if not self.session or self.tasks.busy or not self._require_ai():
            return
        contest = deepcopy(self.session.contest)
        def work(progress):
            return statement_pdf.generate_round_pdf(contest, lambda phase, done, total: progress(f'{phase}：{done}/{total}'))
        def ready(path):
            self.session.pdf_path = str(path)
            self.save(notify=True)
            self.status.set('中文题面已生成：' + str(path))
            messagebox.showinfo('题面已生成', str(path), parent=self.root)
        self._run('正在生成中文题面', work, ready)

    def begin(self):
        if not self.session or self.session.phase != 'prepared' or self.tasks.busy:
            return
        try:
            minutes, handles = self._prep_values()
        except ValueError as exc:
            messagebox.showerror('请检查赛前配置', str(exc), parent=self.root)
            return
        if not messagebox.askyesno('确认开赛', f'共 {len(self.session.contest["problems"])} 题 · {minutes} 分钟\nCF 账号：{", ".join(handles) or "未填写（可手工记录成绩）"}\n\n开始后按真实时间计时，关闭程序不会暂停。确认开始？', parent=self.root):
            return
        previous = deepcopy(self.session)
        self.session.minutes, self.session.handles = minutes, handles
        self.session.start()
        if not self.save(notify=True):
            self.session = previous
            return
        self.status.set('比赛已开始，逐题结果和备注会自动保存。')
        self._controls()
        self._update_phase()

    def finish(self):
        if self.session and self.session.phase == 'running' and not self.tasks.busy:
            if messagebox.askyesno('提前结束比赛', '确认现在结束？本场提交统计窗口将按当前时刻截止，并保留配置的宽限时间。', parent=self.root):
                self.session.finish()
                self.save(notify=True)
                self._update_phase()
                self._controls()

    def _update_phase(self):
        if not self.session:
            return
        self.phase.set(PHASE_NAMES[self.session.phase] + ' · ' + self.session.contest.get('contest_name', '模拟赛'))
        left = self.session.seconds_left()
        self.timer.set(('待开赛 · ' if self.session.phase == 'prepared' else '剩余 ') + f'{left//3600:02d}:{left//60%60:02d}:{left%60:02d}'
                       if self.session.phase in ('prepared', 'running') else '本场已结束')
        elapsed = (time.time() - self.session.started_at) / 60 if self.session.started_at else 0
        for letter, (label, p) in self.difficulties.items():
            level = p.get('difficulty')
            if self.session.phase in ('ended', 'reviewed') or (self.session.phase == 'running' and level and elapsed >= config.DIFFICULTY_REVEAL_MINUTES.get(level, self.session.minutes)):
                text = DIFFICULTIES.get(level, '未知（缺少统计数据）')
                if p.get('accepted_teams') is not None:
                    text += f' · 原场 AC {p["accepted_teams"]} 队'
                label.configure(text='难度：' + text)
            else:
                label.configure(text='难度：待揭晓')

    def _tick(self):
        if self.closed:
            return
        if self.session:
            if self.session.refresh():
                self.save()
                self.status.set('时间到，请抓取评测结果或手动确认各题状态，再提交复盘。')
                self._controls()
                messagebox.showinfo('时间到', '模拟赛已结束。请核对逐题结果；没有记录的题目保留为待确认。', parent=self.root)
            self._update_phase()
        self.root.after(500, self._tick)

    def _load_session(self, session):
        self.loading = True
        self.session = session
        self.retry = None
        self.minutes.set(str(session.minutes))
        self.handles.set(', '.join(session.handles))
        self.notes.configure(state='normal')
        self.notes.delete('1.0', 'end')
        self.notes.insert('1.0', session.notes)
        self.notes.edit_modified(False)
        self.render()
        self.loading = False
        self.dirty = False
        self.save_status.set('训练已载入 · 改动将自动保存')
        self._update_phase()
        self._controls()

    def render(self):
        for widget in self.cards.winfo_children():
            widget.destroy()
        self.fields.clear()
        self.editors.clear()
        self.difficulties.clear()
        if not self.session:
            return
        auto = derive_results(self.session.contest, self.session.entries)
        for p in self.session.contest['problems']:
            letter = p['letter']
            box = ttk.LabelFrame(self.cards, text=f'{letter}. {p["title"]}', padding=10)
            box.pack(fill='x', pady=(0, 8))
            ttk.Label(box, text=p['source']).pack(anchor='w')
            difficulty = ttk.Label(box, foreground='#9a6700')
            difficulty.pack(anchor='w')
            self.difficulties[letter] = (difficulty, p)
            link = ttk.Label(box, text=p['url'], foreground='#1a5fb4', cursor='hand2')
            link.pack(anchor='w', pady=(2, 8))
            link.bind('<Button-1>', lambda _e, url=p['url']: webbrowser.open(url))
            row = ttk.Frame(box)
            row.pack(fill='x')
            detail = self.session.manual.get(letter, {})
            variables = {key: tk.StringVar(value=detail.get(key, default)) for key, default in
                         [('status', '自动'), ('member', ''), ('minutes', ''), ('note', '')]}
            variables['auto'] = tk.StringVar(value='自动记录：' + auto[letter])
            self.fields[letter] = variables
            ttk.Label(row, text='结果').pack(side='left')
            result_input = ttk.Combobox(row, values=STATUSES, textvariable=variables['status'], state='readonly', width=10)
            result_input.pack(side='left', padx=(4, 12))
            self.editors.append(result_input)
            ttk.Label(row, textvariable=variables['auto'], foreground='#666').pack(side='left', padx=(0, 12))
            ttk.Label(row, text='队员').pack(side='left')
            member_input = ttk.Entry(row, textvariable=variables['member'], width=12)
            member_input.pack(side='left', padx=(4, 12))
            self.editors.append(member_input)
            ttk.Label(row, text='耗时（分钟）').pack(side='left')
            time_input = ttk.Entry(row, textvariable=variables['minutes'], width=7)
            time_input.pack(side='left', padx=(4, 0))
            self.editors.append(time_input)
            note_row = ttk.Frame(box)
            note_row.pack(fill='x', pady=(8, 0))
            ttk.Label(note_row, text='备注').pack(side='left')
            note_input = ttk.Entry(note_row, textvariable=variables['note'])
            note_input.pack(side='left', fill='x', expand=True, padx=(4, 0))
            self.editors.append(note_input)
            for key, variable in variables.items():
                if key != 'auto':
                    variable.trace_add('write', lambda *_args, l=letter: self._field_changed(l))
        self.canvas.yview_moveto(0)

    def fetch(self):
        if not self.session or self.session.phase not in ('running', 'ended') or self.tasks.busy:
            return
        if not self.session.handles:
            messagebox.showinfo('未配置参赛账号', '本场未填写 CF 账号，可手动确认成绩；下次请在赛前填写账号。', parent=self.root)
            return
        self.save()
        snapshot = deepcopy(self.session)
        grace = config.VERDICT_GRACE_MINUTES
        try:
            since, until = snapshot.verdict_window(grace)
        except ValueError as exc:
            messagebox.showerror('无法抓取', str(exc), parent=self.root)
            return
        def work(progress):
            values, errors = {}, []
            for index, handle in enumerate(snapshot.handles, 1):
                progress(f'抓取 CF 提交：{handle}（{index}/{len(snapshot.handles)}）')
                try:
                    values[handle] = cf_submit.fetch_verdicts(handle, snapshot.contest['problems'], since, until)
                except Exception as exc:
                    errors.append(f'{handle}: {exc}')
            if not values:
                raise RuntimeError('\n'.join(errors) or '没有可查询账号。')
            return values, errors
        def apply(value):
            values, errors = value
            for handle, verdicts in values.items():
                self.session.merge_verdicts(handle, verdicts, grace, until)
            self._refresh_auto_results()
            self.save(notify=True)
            self.status.set('抓取完成，人工确认不会被覆盖；无记录的题目仍为待确认。' + (' 部分账号失败，可重试。' if errors else ''))
            if errors:
                messagebox.showwarning('部分账号抓取失败', '\n'.join(errors), parent=self.root)
        self._run('正在抓取 CF 结果', work, apply)

    def _refresh_auto_results(self):
        results = derive_results(self.session.contest, self.session.entries)
        for letter, fields in self.fields.items():
            fields['auto'].set('自动记录：' + results[letter])

    def review(self):
        if not self.session or self.session.phase not in ('running', 'ended') or self.tasks.busy:
            return
        self.session.notes = self.notes.get('1.0', 'end-1c')
        try:
            notes = self.session.review_notes()
        except ValueError as exc:
            messagebox.showerror('请检查逐题记录', str(exc), parent=self.root)
            return
        if not self._require_ai():
            return
        results = self.session.results()
        counts = Counter(results.values())
        summary = '、'.join(f'{key} {counts[key]} 题' for key in ('AC', '尝试未过', '未做', '待确认'))
        ending = '\n比赛仍在进行，确认后将以当前时刻结束本场。' if self.session.phase == 'running' else ''
        if not messagebox.askyesno('确认结果并复盘', summary + '\n\n请先核对题目卡片，或抓取最新评测结果。\n待确认表示信息不足，不会作为“未做”评价。' + ending + '\n\n确认提交给 AI 复盘？', parent=self.root):
            return
        if self.session.phase == 'running':
            self.session.finish()
        if not self.save(notify=True):
            return
        snapshot = deepcopy(self.session)
        try:
            memory = load_memory()
        except (ValueError, OSError) as exc:
            messagebox.showerror('无法读取队伍记忆', str(exc), parent=self.root)
            return
        # 有已成功取得但未完整落盘的分析时，仅重试保存，避免重复付费。
        if self.session.analysis:
            self._complete_review(self.session.analysis, results, notes)
            self._controls()
            return
        def analyzed(value):
            self.session.analysis = validate_analysis(value)
            self._complete_review(value, results, notes)
        self._run('AI 正在复盘', lambda _p: validate_analysis(analyze_contest(memory, snapshot.contest, results, notes, snapshot.entries)), analyzed)

    def _complete_review(self, analysis, results, notes):
        # 先保存分析快照；任一步失败可重新打开并继续保存，不先清除本场记录。
        if not self.save(notify=True):
            return
        contest = self.session.to_dict()
        try:
            path = save_summary_markdown(contest, results, analysis, notes, self.session.entries)
            record_contest(contest, results, analysis, self.session.entries)
            self.session.report_path = path
            self.session.phase = 'reviewed'
            if not self.save(notify=True):
                self.session.phase = 'ended'
                return
        except Exception as exc:
            self.status.set('分析已保留，但报告或历史保存失败。修复目录后可重新点击复盘继续保存。')
            messagebox.showerror('复盘保存失败', str(exc), parent=self.root)
            return
        self.status.set('复盘完成，报告、历史与队伍记忆已保存。')
        try:
            self._archive()
        except OSError:
            self.status.set('复盘已保存；额外存档副本写入失败，当前训练存档仍可恢复。')
        self._controls()
        self._update_phase()
        self.show_report()

    def _archive(self):
        directory = Path(config._BASE_DIR) / 'memory' / 'sessions'
        if self.session:
            save_json(directory / (self.session.contest_id + '.json'), self.session.to_dict())
        elif Path(config.CURRENT_CONTEST_FILE).exists():
            # 即使启动时未恢复，切换赛卷前也保留旧文件，包括损坏的原始内容。
            directory.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(config.CURRENT_CONTEST_FILE, directory / f'previous-{time.time_ns()}.json')

    def restore_file(self):
        if self.tasks.busy:
            return
        path = filedialog.askopenfilename(parent=self.root, title='恢复训练 JSON 存档',
            initialdir=str(Path(config._BASE_DIR)/'memory'/'sessions'), filetypes=[('JSON', '*.json')])
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
            restored = TrainingSession.from_dict(data, config.CONTEST_DURATION_MINUTES)
            if self.session and not messagebox.askyesno('切换训练', '当前训练会保存为存档，确认载入所选训练？', parent=self.root):
                return
            if not self.save(notify=True):
                return
            self._archive()
            save_current_contest(restored.to_dict())
            self._load_session(restored)
        except (OSError, ValueError, TypeError) as exc:
            messagebox.showerror('无法恢复', str(exc), parent=self.root)

    def show_memory(self):
        try:
            self._text_window('队伍记忆', json.dumps(load_memory(), ensure_ascii=False, indent=2))
        except (ValueError, OSError) as exc:
            messagebox.showerror('读取失败', str(exc), parent=self.root)

    def show_report(self):
        if not self.session or not self.session.report_path:
            return
        try:
            text = Path(self.session.report_path).read_text(encoding='utf-8')
        except OSError:
            messagebox.showerror('报告文件不可用', '报告可能被移动。分析内容仍保留在训练存档中。', parent=self.root)
            self._text_window('复盘内容', json.dumps(self.session.analysis, ensure_ascii=False, indent=2))
            return
        self._text_window('复盘报告', text)

    def _text_window(self, title, content):
        window = tk.Toplevel(self.root)
        window.title(title)
        window.geometry('820x650')
        text = tk.Text(window, wrap='word', padx=12, pady=12)
        scroll = ttk.Scrollbar(window, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        text.pack(fill='both', expand=True)
        text.insert('1.0', content)
        text.configure(state='disabled')

    def login_cf(self):
        handle, password = config.CF_HANDLE, config.CF_PASSWORD
        def work(_p):
            ok, message = cf_submit.login(handle, password)
            if not ok:
                raise RuntimeError(message)
            return message
        def success(message):
            messagebox.showinfo('CF 登录', message, parent=self.root)
        self._run('请在 Edge 中完成 CF 登录和验证', work, success)

    def _startup(self):
        if needs_setup():
            self.open_settings()
        try:
            saved = load_current_contest()
            if saved:
                log = load_submit_log()
                legacy = log.get('entries', []) if log.get('contest_name') == saved.get('contest_name') else []
                session = TrainingSession.from_dict(saved, config.CONTEST_DURATION_MINUTES, legacy)
                if messagebox.askyesno('恢复上次训练', f'发现「{session.contest.get("contest_name", "模拟赛")}」。\n状态：{PHASE_NAMES[session.phase]}。\n是否恢复题目、逐题记录及备注？', parent=self.root):
                    self._load_session(session)
                    self.status.set('已恢复。进行中的比赛按原开赛时间继续，关闭期间的时间也计入。')
        except (ValueError, OSError, TypeError) as exc:
            messagebox.showwarning('训练存档无法恢复', str(exc), parent=self.root)
        self.refresh_bank()
        if not getattr(sys, 'frozen', False):
            missing = [m for m in ('requests', 'playwright', 'reportlab', 'pypdf') if importlib.util.find_spec(m) is None]
            if missing:
                messagebox.showwarning('部分依赖缺失', '请运行 install.bat 安装依赖：' + ', '.join(missing), parent=self.root)

    def close(self):
        if self.tasks.busy and not messagebox.askyesno('后台任务未完成', '退出后未完成的后台任务需要重新运行。当前训练记录会保存，是否退出？', parent=self.root):
            return
        if not self.save(notify=True):
            return
        self.closed = True
        self.tasks.closed = True
        self.root.destroy()


def main():
    root = tk.Tk()
    TrainingApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
