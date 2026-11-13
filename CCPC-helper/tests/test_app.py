from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
import main
from training_session import TrainingSession
from storage import read_json
from test_training import contest, bank


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root=tk.Tk()
            cls.root.destroy()
        except tk.TclError:
            raise unittest.SkipTest('GUI 检查需要桌面或 Xvfb。')

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        base=Path(self.temp.name)
        names={'_BASE_DIR':str(base), 'CURRENT_CONTEST_FILE':str(base/'memory/current_contest.json'),
               'SUBMIT_LOG_FILE':str(base/'memory/submissions.json'), 'BANK_FILE':str(base/'memory/problem_bank.json'),
               'MEMORY_FILE':str(base/'memory/team_memory.json'), 'HISTORY_FILE':str(base/'memory/history.json'),
               'SUMMARY_DIR':str(base/'memory/summaries'), 'API_KEY':'fake-test-key', 'MODEL':'test-model', 'SETTINGS_ERROR':''}
        self.patches=[]
        for name,value in names.items():
            patcher=patch.object(config,name,value)
            patcher.start()
            self.patches.append(patcher)
        self.dialogs=[]
        for name in ('showinfo','showerror','showwarning','askyesno'):
            patcher=patch.object(main.messagebox,name,return_value=True)
            patcher.start()
            self.dialogs.append(patcher)
        self.root=tk.Tk()
        self.app=main.TrainingApp(self.root,startup=False)
        self.root.update()
        self.app._load_session(TrainingSession(contest(),10,['member']))
        self.app.show_report=lambda:None

    def tearDown(self):
        self.app.closed=True
        self.app.tasks.closed=True
        if self.root.winfo_exists():
            for callback in self.root.tk.call('after','info'):
                self.root.after_cancel(callback)
            self.root.destroy()
        for patcher in reversed(self.dialogs+self.patches):
            patcher.stop()
        self.temp.cleanup()

    def pump(self, timeout=4):
        deadline=time.monotonic()+timeout
        while self.app.tasks.busy and time.monotonic()<deadline:
            self.root.update()
            time.sleep(.01)
        self.root.update()
        self.assertFalse(self.app.tasks.busy)

    def test_notes_manual_results_and_running_recovery(self):
        self.app.fields['A']['status'].set('AC')
        self.app.fields['A']['note'].set('队友提交')
        self.app.notes.insert('1.0','现场备注')
        self.root.update()
        self.app.begin()
        self.assertEqual(self.app.session.phase,'running')
        self.assertTrue(self.app.save())
        data=read_json(config.CURRENT_CONTEST_FILE)
        restored=TrainingSession.from_dict(data,now=self.app.session.started_at+100)
        self.assertEqual(restored.seconds_left(self.app.session.started_at+100),500)
        self.assertEqual(restored.results()['A'],'AC')
        self.assertEqual(restored.manual['A']['note'],'队友提交')
        self.assertEqual(restored.notes,'现场备注')

    def test_generation_is_background_and_not_automatically_started(self):
        from bank_manager import import_bank
        many=[]
        for index in range(12):
            many.append(dict(bank()[0],id=str(index),letter=chr(65+index),url=f'https://codeforces.com/gym/104065/problem/{chr(65+index)}'))
        import_bank(config.BANK_FILE,many)
        release=threading.Event()
        def generated(*_args):
            release.wait(2)
            return contest()
        with patch.object(main,'generate_contest',side_effect=generated) as ai:
            self.app.generate()
            self.assertTrue(self.app.tasks.busy)
            self.assertEqual(str(self.app.buttons['generate'].cget('state')),'disabled')
            heartbeat=[]
            self.root.after(10,lambda:heartbeat.append(True))
            deadline=time.monotonic()+.2
            while time.monotonic()<deadline:
                self.root.update()
                time.sleep(.01)
            self.assertTrue(heartbeat)
            self.app.generate()
            self.assertEqual(ai.call_count,1)
            release.set()
            self.pump()
        self.assertEqual(self.app.session.phase,'prepared')
        self.assertEqual(self.app.session.started_at,0)
        self.assertTrue(list((Path(config._BASE_DIR)/'memory/sessions').glob('*.json')))

    def test_empty_bank_never_calls_ai(self):
        with patch.object(main,'generate_contest') as ai:
            self.app.generate()
            ai.assert_not_called()
        self.assertFalse(self.app.tasks.busy)

    def test_cf_snapshot_uses_deadline_and_does_not_override_manual(self):
        self.app.session.start(time.time()-700)
        self.app.session.refresh()
        self.app.fields['A']['status'].set('未做')
        start=self.app.session.started_at
        with patch.object(main.cf_submit,'fetch_verdicts',return_value={'A':[('AC',start+100),('WA',start+200)], 'B':[('AC',start+9999)]}) as fetch:
            self.app.fetch()
            self.pump()
            self.assertLessEqual(fetch.call_args.args[3],start+600+config.VERDICT_GRACE_MINUTES*60)
        self.assertEqual(self.app.session.results()['A'],'未做')
        self.assertEqual(self.app.session.results()['B'],'待确认')
        self.app.fields['A']['status'].set('自动')
        self.assertEqual(self.app.session.results()['A'],'AC')

    def test_review_persists_snapshot_and_report_once(self):
        self.app.session.start(time.time()-700)
        self.app.session.refresh()
        self.app.fields['A']['status'].set('AC')
        analysis={'summary':'测试复盘','weaknesses':[], 'strengths':[], 'team_advice':'建议','next_focus':[]}
        with patch.object(main,'analyze_contest',return_value=analysis) as ai:
            self.app.review()
            self.assertTrue(self.app.tasks.busy)
            self.assertEqual(str(self.app.editors[0].cget('state')),'disabled')
            self.pump()
            self.assertEqual(ai.call_count,1)
            self.app.review()
            self.assertEqual(ai.call_count,1)
        self.assertEqual(self.app.session.phase,'reviewed')
        self.assertTrue(Path(self.app.session.report_path).exists())
        self.assertEqual(len(read_json(config.HISTORY_FILE)),1)
        self.assertEqual(read_json(config.CURRENT_CONTEST_FILE)['_training']['phase'],'reviewed')

    def test_bad_ai_output_keeps_training_and_allows_retry(self):
        self.app.session.start(time.time()-700)
        self.app.session.refresh()
        self.app.notes.insert('1.0','不能丢失')
        self.root.update()
        with patch.object(main,'analyze_contest',return_value={}):
            self.app.review()
            self.pump()
        self.assertEqual(self.app.session.phase,'ended')
        self.assertEqual(read_json(config.CURRENT_CONTEST_FILE)['_training']['notes'],'不能丢失')
        self.assertFalse(Path(config.HISTORY_FILE).exists())
        self.assertIsNotNone(self.app.retry)
        self.app.fields['B']['note'].set('修正数据')
        self.assertIsNone(self.app.retry)

    def test_failed_report_save_reuses_analysis_without_another_api_call(self):
        self.app.session.start(time.time()-700)
        self.app.session.refresh()
        analysis={'summary':'测试','weaknesses':[]}
        with patch.object(main,'analyze_contest',return_value=analysis) as ai:
            with patch.object(main,'save_summary_markdown',side_effect=OSError('disk unavailable')):
                self.app.review()
                self.pump()
            self.assertEqual(self.app.session.phase,'ended')
            self.assertEqual(self.app.session.analysis,analysis)
            self.app.review()
            self.assertEqual(ai.call_count,1)
            self.assertEqual(self.app.session.phase,'reviewed')

    def test_debounced_autosave_and_startup_resume(self):
        self.app.session.start(time.time()-100)
        self.app.fields['A']['note'].set('自动保存的逐题备注')
        self.app.notes.insert('1.0','自动保存的队伍备注')
        deadline=time.monotonic()+.65
        while time.monotonic()<deadline:
            self.root.update()
            time.sleep(.01)
        stored=read_json(config.CURRENT_CONTEST_FILE)
        self.assertEqual(stored['_training']['notes'],'自动保存的队伍备注')
        self.assertEqual(stored['_training']['manual']['A']['note'],'自动保存的逐题备注')
        self.app.session=None
        with patch.object(main,'needs_setup',return_value=False):
            self.app._startup()
        self.assertEqual(self.app.session.phase,'running')
        self.assertGreater(self.app.session.seconds_left(),490)
        self.assertLessEqual(self.app.session.seconds_left(),500)
        self.assertEqual(self.app.notes.get('1.0','end-1c'),'自动保存的队伍备注')

    def test_start_refuses_to_run_if_primary_save_fails(self):
        with patch.object(main,'save_current_contest',side_effect=OSError('denied')):
            self.app.begin()
        self.assertEqual(self.app.session.phase,'prepared')
        self.assertEqual(self.app.session.started_at,0)

    def test_legacy_log_failure_does_not_undo_saved_start(self):
        with patch.object(main,'save_submit_log',side_effect=OSError('denied')):
            self.app.begin()
        self.assertEqual(self.app.session.phase,'running')
        self.assertEqual(read_json(config.CURRENT_CONTEST_FILE)['_training']['phase'],'running')

    def test_ui_bank_import_and_invalid_file_preserve_existing(self):
        incoming=Path(self.temp.name)/'incoming.json'
        incoming.write_text(json.dumps({'problems':bank()}))
        with patch.object(main.filedialog,'askopenfilename',return_value=str(incoming)):
            self.app.import_bank_dialog()
            self.pump()
        before=Path(config.BANK_FILE).read_bytes()
        self.assertEqual(len(read_json(config.BANK_FILE)['problems']),3)
        incoming.write_text('{bad')
        with patch.object(main.filedialog,'askopenfilename',return_value=str(incoming)):
            self.app.import_bank_dialog()
            self.pump()
        self.assertEqual(Path(config.BANK_FILE).read_bytes(),before)

    def test_partial_history_save_retry_does_not_duplicate_contest(self):
        self.app.session.start(time.time()-700)
        self.app.session.refresh()
        analysis={'summary':'测试','weaknesses':[]}
        with patch.object(main,'analyze_contest',return_value=analysis) as ai:
            with patch('memory_manager.merge_weaknesses',side_effect=[OSError('disk unavailable'),None]):
                self.app.review()
                self.pump()
                self.assertEqual(self.app.session.phase,'ended')
                self.app.review()
                self.assertEqual(self.app.session.phase,'reviewed')
            self.assertEqual(ai.call_count,1)
        self.assertEqual(len(read_json(config.HISTORY_FILE)),1)
