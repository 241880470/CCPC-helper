from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
import contest
import recover_round
from bank_manager import import_bank
from training_session import parse_handles


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        base=Path(self.temp.name)
        self.bank_patch=patch.object(config,'BANK_FILE',str(base/'bank.json'))
        self.history_patch=patch.object(config,'HISTORY_FILE',str(base/'history.json'))
        self.bank_patch.start()
        self.history_patch.start()

    def tearDown(self):
        self.bank_patch.stop()
        self.history_patch.stop()
        self.temp.cleanup()

    def test_backend_empty_or_invalid_bank_never_invokes_ai(self):
        with patch.object(contest,'ask_gpt') as ai:
            with self.assertRaises(ValueError):
                contest.generate_contest({},12)
            Path(config.BANK_FILE).write_text('{bad')
            with self.assertRaises(ValueError):
                contest.generate_contest({},12)
            ai.assert_not_called()

    def test_normalized_bank_selection_ignores_model_invented_urls(self):
        problems=[{'title':'Test '+chr(65+i),'source':'Test source','url':f'https://codeforces.com/gym/104065/problem/{chr(65+i)}'} for i in range(12)]
        normalized=import_bank(config.BANK_FILE,problems)
        picked=[{'bank_id':p['id'],'url':'https://invented.example/','reason':'Test'} for p in normalized]
        with patch.object(contest,'ask_gpt',return_value={'picked':picked}):
            result=contest.generate_contest({},12)
        self.assertTrue(result['from_bank'])
        self.assertEqual({p['url'] for p in result['problems']},{p['url'] for p in normalized})
        self.assertEqual(len(result['problems']),12)
        with patch.object(contest,'ask_gpt',return_value={'picked':[{'bank_id':'made-up'}]}):
            with self.assertRaises(ValueError):
                contest.generate_contest({},12)

    def test_recovery_tool_uses_same_ac_and_unknown_rules(self):
        results=recover_round._derive_results([{'letter':'A'},{'letter':'B'}],[{'problem':'A','result':'AC'},{'problem':'A','result':'WA'}])
        self.assertEqual(results,{'A':'AC','B':'待确认'})

    def test_duplicate_handles_are_case_insensitive(self):
        self.assertEqual(parse_handles('One, one，Two'),['One','Two'])
