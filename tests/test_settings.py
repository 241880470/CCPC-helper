import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
import settings_store as store
import settings_dialog as ui


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()

    def tearDown(self):
        self.environment.stop()

    def test_read_missing_and_malformed_without_secret_in_error(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'settings.json'
            self.assertEqual(store.read_settings(p), {})
            for text in ('{"API_KEY":"private-value",}', '[]', '{"API_KEY":123}'):
                p.write_text(text)
                with self.assertRaises(ValueError) as caught:
                    store.read_settings(p)
                self.assertNotIn('private-value', str(caught.exception))

    def test_environment_priority_and_validation(self):
        with patch.dict(os.environ, {'CCPC_AI_API_KEY':'environment'}, clear=True):
            values = store.effective_settings({'API_KEY':'local'})
            self.assertEqual(values['API_KEY'], 'environment')
        for url in ('wrong', 'https://', 'https://user:password@example.com', 'https://example.com:bad'):
            with self.assertRaises(ValueError):
                store.validate_settings(dict(store.DEFAULTS, API_KEY='test', BASE_URL=url))
        store.validate_settings(dict(store.DEFAULTS, API_KEY='test', BASE_URL=''))
        with self.assertRaises(ValueError):
            store.validate_settings(dict(store.DEFAULTS, API_KEY='YOUR_API_KEY'))

    def test_atomic_write_failure_keeps_original(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'settings.json'
            store.write_settings(p, {'API_KEY':'old'})
            with patch.object(store.os, 'replace', side_effect=OSError('disk unavailable')):
                with self.assertRaises(OSError):
                    store.write_settings(p, {'API_KEY':'new'})
            self.assertEqual(store.read_settings(p), {'API_KEY':'old'})
            self.assertEqual(list(Path(d).glob('.settings-*')), [])
            if os.name == 'posix':
                self.assertEqual(p.stat().st_mode & 0o777, 0o600)

    def test_cached_api_client_updates_after_save(self):
        import AI_agent as ai
        with patch.object(ai, 'OpenAI') as constructor:
            ai._client = None
            config.apply_connection_settings({'API_KEY':'first'})
            first = ai._get_client()
            self.assertIs(first, ai._get_client())
            self.assertEqual(constructor.call_count, 1)
            config.apply_connection_settings({'API_KEY':'second', 'BASE_URL':'https://example.com/v1'})
            ai._get_client()
            self.assertEqual(constructor.call_count, 2)
            self.assertEqual(constructor.call_args.kwargs['api_key'], 'second')


class DialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tkinter as tk
        cls.root = tk.Tk()
        cls.root.geometry('960x760+0+0')
        cls.root.update()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_path = config._SETTINGS_FILE
        config._SETTINGS_FILE = str(Path(self.temp.name)/'settings.json')
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        config.apply_connection_settings({})
        self.dialog = None

    def tearDown(self):
        if self.dialog is not None and self.dialog.winfo_exists():
            self.dialog.destroy()
        config._SETTINGS_FILE = self.old_path
        self.env.stop()
        self.temp.cleanup()

    def open(self, first=True):
        self.dialog = ui.SettingsDialog(self.root, first_run=first)
        self.root.update()
        return self.dialog

    def fill(self, dialog):
        dialog.variables['API_KEY'].set('test-key')
        dialog.variables['CF_HANDLE'].set('test-handle')
        dialog.variables['CF_PASSWORD'].set(' password with spaces ')

    def test_first_run_skip_and_secret_masks(self):
        self.assertTrue(ui.needs_setup())
        d = self.open()
        for key in ('API_KEY','CF_PASSWORD','CF_API_KEY','CF_API_SECRET'):
            self.assertEqual(d.entries[key].cget('show'), '*')
        d.show_secrets.set(True)
        d._toggle_secrets()
        self.assertEqual(d.entries['API_KEY'].cget('show'), '')
        d.destroy()
        self.assertFalse(d.saved)
        self.assertFalse(Path(config._SETTINGS_FILE).exists())
        self.assertTrue(ui.needs_setup())

    def test_save_session_password_and_reopen(self):
        d = self.open()
        self.fill(d)
        d._save()
        self.assertTrue(d.saved)
        self.assertFalse(ui.needs_setup())
        saved = store.read_settings(config._SETTINGS_FILE)
        self.assertEqual(saved['API_KEY'], 'test-key')
        self.assertNotIn('CF_PASSWORD', saved)
        self.assertEqual(config.CF_PASSWORD, ' password with spaces ')
        d = self.open(first=False)
        self.assertEqual(d.variables['API_KEY'].get(), 'test-key')
        self.assertFalse(d.remember_password.get())
        d.destroy()
        config.apply_connection_settings(saved)
        self.assertEqual(config.CF_PASSWORD, '')

    def test_remember_and_remove_password(self):
        d = self.open()
        self.fill(d)
        d.remember_password.set(True)
        d._save()
        self.assertEqual(store.read_settings(config._SETTINGS_FILE)['CF_PASSWORD'], ' password with spaces ')
        d = self.open(False)
        self.assertTrue(d.remember_password.get())
        d.remember_password.set(False)
        d._save()
        self.assertNotIn('CF_PASSWORD', store.read_settings(config._SETTINGS_FILE))

    def test_invalid_input_and_unwritable_save_preserve_state(self):
        d = self.open()
        with patch.object(ui.messagebox, 'showerror') as error:
            d._save()
            self.assertEqual(error.call_count, 1)
        self.assertTrue(d.winfo_exists())
        self.assertFalse(Path(config._SETTINGS_FILE).exists())
        self.fill(d)
        with patch.object(ui, 'write_settings', side_effect=OSError('denied')):
            with patch.object(ui.messagebox, 'showerror'):
                d._save()
        self.assertTrue(d.winfo_exists())
        self.assertEqual(config.API_KEY, '')

    def test_environment_fields_readonly_not_copied_to_file(self):
        os.environ['CCPC_AI_API_KEY'] = 'env-only-key'
        os.environ['CCPC_AI_MODEL'] = 'env-model'
        config.apply_connection_settings({})
        d = self.open()
        self.assertEqual(str(d.entries['API_KEY'].cget('state')), 'readonly')
        d.provider.set('OpenAI')
        d._choose_provider()
        self.assertEqual(d.variables['MODEL'].get(), 'env-model')
        self.assertEqual(d.variables['BASE_URL'].get(), '')
        d._save()
        self.assertNotIn('API_KEY', store.read_settings(config._SETTINGS_FILE))
        self.assertEqual(config.API_KEY, 'env-only-key')

    def test_corrupt_config_backed_up_before_repair(self):
        path = Path(config._SETTINGS_FILE)
        broken = b'{"API_KEY": "old-private",}'
        path.write_bytes(broken)
        config.SETTINGS_ERROR = 'invalid file'
        d = self.open()
        self.fill(d)
        d._save()
        backups = list(path.parent.glob('settings.invalid-*.json'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), broken)
        self.assertFalse(config.SETTINGS_ERROR)
        self.assertEqual(store.read_settings(path)['API_KEY'], 'test-key')


if __name__ == '__main__':
    unittest.main(verbosity=2)
