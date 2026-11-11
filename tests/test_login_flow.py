"""Interactive login tests use synthetic entries and an injected local UI."""

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from passwords_bridge import broker, client
from passwords_bridge.accounts import account_list, resolve_identity
from passwords_bridge.aliases import AliasStore, AliasExists
from passwords_bridge.browser import BridgeError
from passwords_bridge.menu import menu, validate_action
from passwords_bridge.local_ui import LocalUI
from passwords_bridge.broker import SessionExpired
from passwords_bridge.session import CredentialLookupError
from test_session_reuse import FakeSession, Monitor


def login_request(**changes):
    base = {'op': 'login', 'domain': 'example.com', 'username': '', 'alias': '', 'save_alias': '',
            'clear_after': 0, 'idle_seconds': 300, 'display': True, 'display_seconds': 45}
    return dict(base, **changes)


PASSWORDS = [
    {'USR': 'alice', 'PWD': 'SECRET-ALICE', 'customTitle': 'Personal', 'sites': ['example.com']},
    {'USR': 'bob', 'PWD': 'SECRET-BOB', 'customTitle': 'Work', 'sites': ['example.com']},
]


class CatalogSession(FakeSession):
    passwords = PASSWORDS
    codes = [{'username': 'alice', 'domain': 'example.com', 'code': '111111'}]
    otp_queries = 0
    def entries(self, kind):
        user = self.scope[1]
        if kind == 'password':
            return [dict(row) for row in self.passwords if not user or row['USR'] == user]
        self.otp_queries += 1
        return [dict(row, code=str(self.otp_queries).zfill(6)) for row in self.codes
                if not user or row['username'] == user]


class UI:
    def __init__(self, choices=None, actions=None, texts=None, confirm=False):
        self.choices = list(choices or [0])
        self.actions = list(actions or ['done'])
        self.texts = list(texts or [])
        self.confirmed = confirm
        self.pickers, self.panels, self.messages = [], [], []
    def choose(self, rows, *args):
        self.pickers.append([row.identity for row in rows])
        index = self.choices.pop(0) if self.choices else 0
        return None if index is None else rows[index]
    def copied(self, account, value, has_otp, show, timeout, can_password=False):
        self.panels.append((account.kind, value, has_otp, show, can_password))
        return self.actions.pop(0) if self.actions else 'done'
    def text(self, *args): return self.texts.pop(0) if self.texts else None
    def confirm(self, *args): return self.confirmed
    def message(self, title, message): self.messages.append((title, message))


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = AliasStore(self.tmp.name)
        self.instances, self.copies = [], []
        self.factory = type('CatalogFixture', (CatalogSession,), {'instances': self.instances})
        self.ui = UI()
        self.manager = broker.SessionManager(Monitor(), self.factory,
            lambda value, seconds: self.copies.append(value), alias_store=self.store,
            ui_factory=lambda manager, flow: self.ui)
    def tearDown(self):
        self.manager.close_session()
        self.tmp.cleanup()

    def test_password_first_no_automatic_otp_overwrite(self):
        result = self.manager.login(login_request())
        self.assertEqual(self.copies, ['SECRET-ALICE'])
        self.assertTrue(self.ui.panels[0][2])
        self.assertNotIn('SECRET-', json.dumps(result))
        self.assertIsNone(self.instances[0].scope)

    def test_selected_account_not_first_account_is_copied(self):
        self.ui = UI(choices=[1])
        self.manager.login(login_request())
        self.assertEqual(self.copies, ['SECRET-BOB'])
        self.assertFalse(self.ui.panels[0][2])  # Alice's OTP must not be offered for Bob.

    def test_otp_is_fetched_after_confirmation_and_not_from_prefetch(self):
        self.ui = UI(actions=['otp', 'done'])
        self.manager.login(login_request())
        self.assertEqual(self.copies, ['SECRET-ALICE', '000003'])
        self.assertEqual(len(self.ui.pickers), 1)  # one exact OTP needs no second account prompt
        self.assertEqual(self.instances[0].pairs, 1)

    def test_otp_refresh_fetches_another_current_value(self):
        self.ui = UI(actions=['otp', 'refresh', 'done'])
        self.manager.login(login_request())
        self.assertEqual(self.copies, ['SECRET-ALICE', '000003', '000004'])

    def test_otp_only_account_is_listed_and_copied(self):
        self.factory.passwords = []
        self.manager.login(login_request())
        self.assertEqual(self.copies, ['000002'])
        self.assertEqual(self.ui.panels[0][0], 'otp')

    def test_other_user_otp_never_overwrites_password(self):
        self.ui = UI(actions=['otp', 'done'], choices=[1])
        self.manager.login(login_request())
        self.assertEqual(self.copies, ['SECRET-BOB'])
        self.assertTrue(self.ui.messages)

    def test_broader_otp_domain_requires_explicit_choice(self):
        self.factory.codes = [{'username': 'alice', 'domain': 'other.example.com', 'code': '111111'}]
        self.ui = UI(actions=['otp', 'done'], choices=[0, None])
        self.manager.login(login_request())
        self.assertEqual(self.copies, ['SECRET-ALICE'])
        self.assertEqual(len(self.ui.pickers), 2)

    def test_bind_alias_copies_nothing_and_persists_only_metadata(self):
        self.manager.login(login_request(save_alias='personal'))
        self.assertEqual(self.copies, [])
        saved = (Path(self.tmp.name) / 'aliases.json').read_text()
        self.assertNotIn('SECRET-', saved)
        self.assertNotIn('000001', saved)
        self.assertEqual(self.store.get('personal')['identity']['username'], 'alice')

    def test_alias_survives_order_and_password_changes(self):
        self.manager.login(login_request(save_alias='personal'))
        self.ui = UI()
        self.factory.passwords = [PASSWORDS[1], dict(PASSWORDS[0], PWD='NEW-SECRET')]
        self.manager.login(login_request(domain='', alias='personal'))
        self.assertEqual(self.copies, ['NEW-SECRET'])
        self.assertEqual(self.ui.pickers, [])

    def test_stale_alias_never_falls_back_to_another_account(self):
        self.manager.login(login_request(save_alias='personal'))
        self.factory.passwords = [dict(PASSWORDS[0], customTitle='Renamed')]
        with self.assertRaises(CredentialLookupError):
            self.manager.login(login_request(domain='', alias='personal'))
        self.assertEqual(self.copies, [])

    def test_identical_metadata_duplicates_cannot_be_copied_or_aliased(self):
        self.factory.passwords = [PASSWORDS[0], dict(PASSWORDS[0], PWD='DIFFERENT-SECRET')]
        with self.assertRaises(CredentialLookupError): self.manager.login(login_request())
        self.assertEqual(self.copies, [])

    def test_save_alias_from_password_window(self):
        self.ui = UI(actions=['alias', 'done'], texts=['my-github'])
        self.manager.login(login_request())
        self.assertEqual(self.store.get('my-github')['identity']['kind'], 'password')
        self.assertEqual(self.copies, ['SECRET-ALICE'])

    def test_alias_collision_requires_confirmation(self):
        self.manager.login(login_request(save_alias='same'))
        self.ui = UI(choices=[1], confirm=False)
        self.manager.login(login_request(save_alias='same'))
        self.assertEqual(self.store.get('same')['identity']['username'], 'alice')

    def test_picker_cancellation_copies_nothing(self):
        self.ui = UI(choices=[None])
        result = self.manager.login(login_request())
        self.assertTrue(result['ok'])
        self.assertEqual(self.copies, [])


class AliasAndMenuTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = AliasStore(self.tmp.name)
        self.binding = {'domain': 'example.com', 'identity': account_list(PASSWORDS, 'password', 'example.com')[0].identity}
    def tearDown(self): self.tmp.cleanup()

    def test_alias_crud_and_permissions(self):
        self.store.put('work', self.binding)
        self.store.rename('work', '工作')
        self.assertEqual(self.store.get('工作')['domain'], 'example.com')
        self.assertEqual(stat.S_IMODE((Path(self.tmp.name) / 'aliases.json').stat().st_mode), 0o600)
        self.assertTrue(self.store.remove('工作'))
        self.assertEqual(self.store.all(), [])

    def test_alias_cannot_store_secret_fields(self):
        for binding in (dict(self.binding, password='SECRET'),
                        {'domain': 'example.com', 'identity': dict(self.binding['identity'], secret='SECRET')}):
            with self.assertRaises(BridgeError): self.store.put('bad', binding)
        self.assertFalse((Path(self.tmp.name) / 'aliases.json').exists())

    def test_corrupt_alias_file_is_not_overwritten(self):
        path = Path(self.tmp.name) / 'aliases.json'
        path.write_text('broken')
        with self.assertRaises(BridgeError): self.store.put('new', self.binding)
        self.assertEqual(path.read_text(), 'broken')

    def test_menu_is_metadata_only_and_arguments_are_validated(self):
        self.store.put('gh-work', self.binding)
        for query in ('', 'gh', 'example.com', 'alias', 'alias add demo example.com', 'alias rename gh-work new', 'lock'):
            result = menu(query, self.store)
            self.assertNotIn('SECRET-', json.dumps(result))
            for item in result['items']:
                if 'arg' in item: validate_action(json.loads(item['arg']))
        exact = menu('@gh-work', self.store)['items'][0]
        self.assertEqual(json.loads(exact['arg']), {'action': 'login', 'alias': 'gh-work'})

    def test_invalid_action_does_not_become_code(self):
        for action in ({'action': 'eval', 'script': 'bad'}, {'action': 'login', 'query': 'a', 'password': 'SECRET'}):
            with self.assertRaises(BridgeError): validate_action(action)


@unittest.skipUnless(sys.platform == 'darwin', 'AppKit and Darwin peer credentials required')
class InteractiveNativeTests(unittest.TestCase):
    def test_native_picker_and_display_components(self):
        script = Path(__file__).resolve().parents[1] / 'src/passwords_bridge/local_ui.js'
        result = subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', str(script), '--selftest'],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('selftest passed', result.stdout)

    def test_interactive_rpc_never_returns_displayed_secret(self):
        with tempfile.TemporaryDirectory(prefix='pw-ui-ipc-') as root:
            ready = threading.Event()
            copies, panels = [], []
            ui = UI(actions=['otp', 'done'])
            thread = threading.Thread(target=broker.serve, kwargs={
                'root': root, 'monitor_factory': Monitor, 'session_factory': CatalogSession,
                'copier': lambda value, delay: copies.append(value), 'ready': ready,
                'ui_factory': lambda manager, flow: ui})
            thread.start()
            try:
                self.assertTrue(ready.wait(5))
                reply = client.rpc(login_request(), Path(root))
                self.assertTrue(reply['ok'])
                self.assertEqual(copies, ['SECRET-ALICE', '000003'])
                self.assertNotIn('SECRET-', json.dumps(reply))
                self.assertNotIn('000003', json.dumps(reply))
                self.assertNotIn('SECRET-', (Path(root) / 'session.log').read_text())
            finally:
                try: client.rpc({'op': 'stop'}, Path(root))
                except client.Unavailable: pass
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive())


class LocalUITransportTests(unittest.TestCase):
    def test_hidden_display_does_not_send_secret_to_window(self):
        account = account_list(PASSWORDS, 'password', 'example.com')[0]
        ui = LocalUI()
        with patch.object(ui, 'run', return_value={'action': 'done'}) as dialog:
            ui.copied(account, account.value, True, False, 45)
        self.assertEqual(dialog.call_args.args[0]['value'], '')
        self.assertNotIn('SECRET-', json.dumps(dialog.call_args.args[0]))

    def test_plaintext_uses_stdin_not_process_arguments(self):
        original = subprocess.Popen
        captured = []
        def child(args, **kwargs):
            captured.append(args)
            code = 'import json,sys; d=json.load(sys.stdin); assert d["value"]=="SYNTHETIC-SECRET"; print(json.dumps({"action":"done"}))'
            return original([sys.executable, '-c', code], **kwargs)
        with patch('passwords_bridge.local_ui.subprocess.Popen', side_effect=child):
            result = LocalUI().run({'kind': 'secret', 'value': 'SYNTHETIC-SECRET'})
        self.assertEqual(result, {'action': 'done'})
        self.assertNotIn('SYNTHETIC-SECRET', repr(captured))

    def test_lock_guard_terminates_the_owned_window(self):
        original, children = subprocess.Popen, []
        def child(args, **kwargs):
            process = original([sys.executable, '-c', 'import time; time.sleep(20)'], **kwargs)
            children.append(process)
            return process
        def locked(): raise SessionExpired('locked')
        with patch('passwords_bridge.local_ui.subprocess.Popen', side_effect=child), self.assertRaises(SessionExpired):
            LocalUI(guard=locked).run({'kind': 'secret', 'value': 'SYNTHETIC'})
        self.assertIsNotNone(children[0].poll())

    def test_display_timeout_closes_the_window(self):
        original, children = subprocess.Popen, []
        def child(args, **kwargs):
            process = original([sys.executable, '-c', 'import sys,time;sys.stdin.read();time.sleep(20)'], **kwargs)
            children.append(process)
            return process
        with patch('passwords_bridge.local_ui.subprocess.Popen', side_effect=child):
            result = LocalUI(timeout=.2).run({'kind': 'secret', 'value': 'SYNTHETIC'})
        self.assertTrue(result['timed_out'])
        self.assertIsNotNone(children[0].poll())


if __name__ == '__main__': unittest.main()
