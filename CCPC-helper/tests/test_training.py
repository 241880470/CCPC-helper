import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from training_session import TrainingSession, derive_results
from bank_manager import read_bank, import_bank, validate_bank
from storage import read_json, save_json
from background_tasks import BackgroundTasks


def contest():
    return {'contest_name': '测试训练', 'from_bank': True, 'problems': [
        {'letter': c, 'title': f'Test {c}', 'source': 'Test source', 'url': f'https://codeforces.com/gym/104065/problem/{c}', 'reason': '测试'}
        for c in 'ABC']}


def bank():
    return [dict(p, id='104065'+p['letter'], gym_id=104065) for p in contest()['problems']]


class SessionTests(unittest.TestCase):
    def test_preparation_does_not_start_timer(self):
        s = TrainingSession(contest(), 120)
        self.assertEqual(s.seconds_left(999999), 7200)
        self.assertEqual(s.started_at, 0)
        with self.assertRaises(ValueError):
            s.verdict_window()
        s.start(1000)
        with self.assertRaises(ValueError):
            s.start(1100)

    def test_restore_running_preparation_and_expired(self):
        s = TrainingSession(contest(), 10, ['member'])
        s.notes = '队伍备注'
        s.manual = {'A': {'status':'AC', 'member':'队员', 'minutes':'12.5', 'note':'手工确认'}}
        restored = TrainingSession.from_dict(s.to_dict(), now=1000)
        self.assertEqual(restored.phase, 'prepared')
        self.assertEqual(restored.manual, s.manual)
        s.start(1000)
        running = TrainingSession.from_dict(s.to_dict(), now=1100)
        self.assertEqual(running.seconds_left(1100), 500)
        self.assertEqual(running.phase, 'running')
        expired = TrainingSession.from_dict(s.to_dict(), now=2000)
        self.assertEqual(expired.phase, 'ended')
        self.assertEqual(expired.ended_at, 1600)
        self.assertEqual(expired.notes, '队伍备注')

    def test_ac_is_not_overwritten_and_missing_is_unknown(self):
        entries = [{'problem':'A', 'result':'AC'}, {'problem':'A', 'result':'WA'}, {'problem':'B', 'result':'TESTING'}]
        self.assertEqual(derive_results(contest(), entries), {'A':'AC','B':'待确认','C':'待确认'})
        self.assertEqual(derive_results(contest(), entries, {'A':{'status':'未做'}})['A'], '未做')

    def test_cutoff_grace_idempotency_and_multiple_handles(self):
        s = TrainingSession(contest(), 10)
        s.start(1000)
        self.assertEqual(s.verdict_window(2, 9000), (1000, 1720))
        self.assertEqual(s.verdict_window(2, 1100), (1000, 1100))
        data = {'A':[('AC', 1600),('WA', 1650),('AC', 1721),('AC', 999)]}
        s.merge_verdicts('one', data, 2, 9000)
        s.merge_verdicts('one', data, 2, 9000)
        self.assertEqual(len(s.entries), 2)
        s.merge_verdicts('two', {'B':[('AC',1500)]}, 2, 9000)
        self.assertEqual(s.results(), {'A':'AC', 'B':'AC','C':'待确认'})
        s.manual['A'] = {'status':'尝试未过'}
        s.merge_verdicts('one', data, 2, 9000)
        self.assertEqual(s.results()['A'],'尝试未过')
        self.assertEqual(s.results()['B'],'AC')

    def test_early_finish_shortens_window(self):
        s = TrainingSession(contest(), 10)
        s.start(1000)
        s.finish(1200)
        self.assertEqual(s.verdict_window(2, 9000),(1000,1320))
        s.merge_verdicts('one',{'A':[('AC',1500)]},2,9000)
        self.assertEqual(s.results()['A'],'待确认')

    def test_legacy_recovery_and_invalid_save(self):
        data = contest()
        data['started_at'] = ''
        s = TrainingSession.from_dict(data, legacy_entries=[{'problem':'A','result':'AC'}])
        self.assertEqual(s.phase,'ended')
        self.assertEqual(s.results()['A'],'AC')
        invalid = s.to_dict()
        invalid['_training']['manual'] = {'A':'wrong'}
        with self.assertRaises(ValueError):
            TrainingSession.from_dict(invalid)

    def test_invalid_elapsed_time_blocks_review(self):
        s = TrainingSession(contest())
        for elapsed in ('-1','nan','inf','abc'):
            s.manual = {'A':{'minutes':elapsed}}
            with self.assertRaises(ValueError):
                s.review_notes()


class BankStorageTests(unittest.TestCase):
    def test_bank_validation_and_import_backup(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'bank.json'
            first = bank()
            import_bank(path, first)
            updated = [dict(first[0], title='Updated')]
            import_bank(path, updated, read_bank(path))
            self.assertEqual(len(read_bank(path)),3)
            self.assertEqual(read_bank(path)[0]['title'],'Updated')
            self.assertEqual(json.loads(Path(str(path)+'.bak').read_text())['problems'][0]['title'],'Test A')
            original = path.read_bytes()
            bad = [dict(first[0], url='https://example.com/problem/A')]
            with self.assertRaises(ValueError):
                import_bank(path, bad)
            self.assertEqual(path.read_bytes(), original)
        for bad in ([], [bank()[0],bank()[0]], [dict(bank()[0],letter='Z')], [dict(bank()[0],accepted_teams=-1)]):
            with self.assertRaises(ValueError):
                validate_bank(bad)

    def test_atomic_save_failure_and_backup_recovery(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'session.json'
            save_json(path, {'v':1})
            save_json(path, {'v':2})
            with patch('storage.os.replace', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    save_json(path, {'v':3})
            self.assertEqual(read_json(path), {'v':2})
            path.write_text('{bad')
            self.assertEqual(read_json(path), {'v':1})
            save_json(path, {'v':4})
            self.assertEqual(json.loads(Path(str(path)+'.bak').read_text()), {'v':1})


class BackgroundTests(unittest.TestCase):
    def test_worker_is_nonblocking_and_duplicate_requests_rejected(self):
        tasks = BackgroundTasks()
        release = threading.Event()
        def work(progress):
            progress('Working')
            release.wait(2)
            return 42
        self.assertTrue(tasks.start(work))
        self.assertFalse(tasks.start(work))
        self.assertTrue(tasks.busy)
        release.set()
        events=[]
        deadline=time.monotonic()+3
        while tasks.busy and time.monotonic()<deadline:
            events.extend(tasks.poll())
            time.sleep(.01)
        self.assertIn(('success',42),events)
        self.assertIn(('progress','Working'),events)
        self.assertFalse(tasks.busy)
