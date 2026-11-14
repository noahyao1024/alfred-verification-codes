"""Alias text is supplied through Alfred's query, never a separate native field."""

import json
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_apple_passwords import app
from passwords_bridge.alias_editor import AliasEditor
from passwords_bridge.aliases import AliasStore
from passwords_bridge.browser import BridgeError
from passwords_bridge.menu import menu, validate_action


BINDING = {'domain': 'example.com', 'identity': {'kind': 'password', 'username': 'alice',
            'title': 'Work', 'sites': ['example.com']}}


class AliasEditorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = [1000.0]
        self.editor = AliasEditor(self.root, clock=lambda: self.now[0])
        self.store = AliasStore(self.root)
    def tearDown(self): self.tmp.cleanup()

    def test_alfred_text_entry_saves_the_selected_account_without_native_ui(self):
        action = {'action': 'begin_alias', **BINDING}
        with patch.object(app, 'AliasEditor', return_value=self.editor), \
             patch.object(app, 'open_alfred_search') as focus, \
             patch.object(app, 'request_selected') as read, \
             patch.object(app.LocalUI, 'text', side_effect=AssertionError('native alias field must not be used')):
            self.assertEqual(app.interactive(action), (True, ''))
            focus.assert_called_once_with('alias save ')
            before = menu('alias save ', self.store, self.editor)['items'][0]
            self.assertFalse(before['valid'])
            # This is the exact text Script Filter receives as the user types.
            typed = menu('alias save 工作账号', self.store, self.editor)['items'][0]
            save = validate_action(json.loads(typed['arg']))
            self.assertTrue(app.interactive(save)[0])
            read.assert_not_called()
        self.assertEqual(self.store.get('工作账号')['identity'], BINDING['identity'])
        self.assertIsNone(self.editor.current())

    def test_password_window_handoff_contains_metadata_only(self):
        result = {'ok': True, 'message': '', 'alias_target': BINDING}
        with patch.object(app, 'AliasEditor', return_value=self.editor), patch.object(app, 'open_alfred_search') as focus:
            self.assertEqual(app.handle_result(result), (True, ''))
        focus.assert_called_once_with('alias save ')
        self.assertEqual(self.editor.current()['binding'], BINDING)
        self.assertEqual(stat.S_IMODE(self.editor.path.stat().st_mode), 0o600)

    def test_alfred_timeout_keeps_draft_and_shows_manual_recovery(self):
        with patch.object(app, 'AliasEditor', return_value=self.editor), \
             patch.dict(app.os.environ, {'passwordkeyword': 'pass'}), \
             patch.object(app.subprocess, 'run', side_effect=subprocess.TimeoutExpired('osascript', 10)):
            with self.assertRaisesRegex(BridgeError, '请直接输入 pass alias save'):
                app.begin_alias_editor(BINDING)
        self.assertEqual(self.editor.current()['binding'], BINDING)

    def test_stale_result_cannot_bind_a_newly_selected_account(self):
        first = self.editor.begin(BINDING)
        second = self.editor.begin({'domain': 'other.com', 'identity': dict(BINDING['identity'], sites=['other.com'])})
        with self.assertRaises(BridgeError): self.editor.save(first['id'], 'work')
        self.assertEqual(self.store.all(), [])
        self.assertEqual(self.editor.current()['id'], second['id'])

    def test_expired_draft_is_not_saved(self):
        draft = self.editor.begin(BINDING)
        self.now[0] += 301
        with self.assertRaises(BridgeError): self.editor.save(draft['id'], 'work')
        self.assertEqual(self.store.all(), [])

    def test_existing_binding_update_is_visible_before_enter(self):
        self.store.put('work', {'domain': 'old.example', 'identity': dict(BINDING['identity'], sites=['old.example'])})
        self.editor.begin(BINDING)
        item = menu('alias save work', self.store, self.editor)['items'][0]
        self.assertIn('更新已有别名', item['title'])
        self.assertIn('example.com', item['subtitle'])
        action = validate_action(json.loads(item['arg']))
        self.assertTrue(action['replace'])
        self.editor.save(action['draft'], action['name'], action['replace'])
        self.assertEqual(self.store.get('work')['domain'], 'example.com')

    def test_secret_fields_cannot_be_added_to_a_draft(self):
        with self.assertRaises(BridgeError): self.editor.begin(dict(BINDING, password='MUST-NOT-BE-STORED'))
        self.assertFalse(self.editor.path.exists())

    def test_rename_uses_alfred_input_instead_of_native_dialog(self):
        self.store.put('old', BINDING)
        with patch.object(app, 'AliasStore', return_value=self.store), \
             patch.object(app, 'open_alfred_search') as focus, \
             patch.object(app.LocalUI, 'text', side_effect=AssertionError('native field must not be used')):
            self.assertEqual(app.interactive({'action': 'rename', 'name': 'old'}), (True, ''))
            focus.assert_called_once_with('alias rename old ')
            self.assertTrue(app.interactive({'action': 'rename', 'name': 'old', 'new': 'new'})[0])
        self.assertEqual(self.store.get('new')['identity'], BINDING['identity'])


if __name__ == '__main__': unittest.main()
