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
from passwords_bridge.accounts import account_list, resolve_identity, resolve_otp_metadata
from passwords_bridge.aliases import AliasStore, AliasExists
from passwords_bridge.browser import BridgeError
from passwords_bridge.menu import catalog_menu, menu, validate_action
from passwords_bridge.local_ui import LocalUI
from passwords_bridge.broker import SessionExpired
from passwords_bridge.performance import format_log_event, stats_menu
from passwords_bridge.session import CredentialLookupError
from test_session_reuse import FakeSession, Monitor


def login_request(**changes):
    base = {'op': 'login', 'domain': 'example.com', 'username': '', 'alias': '', 'save_alias': '',
            'clear_after': 0, 'idle_seconds': 300, 'display': True, 'display_seconds': 45}
    return dict(base, **changes)


def catalog_request(**changes):
    return dict({'op': 'catalog', 'mode': 'password', 'domain': 'example.com',
                 'idle_seconds': 300}, **changes)


def select_request(identity, **changes):
    return dict({'op': 'select', 'domain': 'example.com', 'identity': identity,
                 'clear_after': 0, 'idle_seconds': 300, 'display': True,
                 'display_seconds': 45}, **changes)


PASSWORDS = [
    {'USR': 'alice', 'PWD': 'SECRET-ALICE', 'customTitle': 'Personal', 'sites': ['example.com']},
    {'USR': 'bob', 'PWD': 'SECRET-BOB', 'customTitle': 'Work', 'sites': ['example.com']},
]


class CatalogSession(FakeSession):
    passwords = PASSWORDS
    codes = [{'username': 'alice', 'domain': 'example.com', 'code': '111111'}]
    password_queries = 0
    password_metadata_queries = 0
    otp_queries = 0
    otp_metadata_queries = 0
    def entries(self, kind):
        user = self.scope[1]
        if kind == 'password_list':
            self.password_metadata_queries += 1
            return [dict(row, PWD='Not Included') for row in self.passwords if not user or row['USR'] == user]
        if kind == 'password':
            self.password_queries += 1
            return [dict(row) for row in self.passwords if not user or row['USR'] == user]
        if kind == 'otp_list':
            self.otp_metadata_queries += 1
            return [{key: value for key, value in row.items() if key != 'code'} for row in self.codes
                    if not user or row['username'] == user]
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
        self.instances, self.copies, self.logs = [], [], []
        self.factory = type('CatalogFixture', (CatalogSession,), {'instances': self.instances})
        self.ui = UI()
        self.manager = broker.SessionManager(Monitor(), self.factory,
            lambda value, seconds: self.copies.append(value), alias_store=self.store,
            ui_factory=lambda manager, flow: self.ui, log=self.logs.append)
    def tearDown(self):
        self.manager.close_session()
        self.tmp.cleanup()

    def test_password_first_no_automatic_otp_overwrite(self):
        result = self.manager.login(login_request())
        self.assertEqual(self.copies, ['SECRET-ALICE'])
        self.assertTrue(self.ui.panels[0][2])
        self.assertNotIn('SECRET-', json.dumps(result))
        self.assertIsNone(self.instances[0].scope)
        self.assertEqual(self.instances[0].otp_queries, 0)
        self.assertEqual(self.instances[0].otp_metadata_queries, 0)
        self.assertTrue(any(event.startswith('perf|time_to_picker|') for event in self.logs))

    def test_selected_account_not_first_account_is_copied(self):
        self.ui = UI(choices=[1])
        self.manager.login(login_request())
        self.assertEqual(self.copies, ['SECRET-BOB'])
        self.assertTrue(self.ui.panels[0][2])  # OTP discovery is deferred until the button is used.

    def test_otp_is_fetched_after_confirmation_and_not_from_prefetch(self):
        self.ui = UI(actions=['otp', 'done'])
        self.manager.login(login_request())
        self.assertEqual(self.copies, ['SECRET-ALICE', '000001'])
        self.assertEqual(len(self.ui.pickers), 1)  # one exact OTP needs no second account prompt
        self.assertEqual(self.instances[0].pairs, 1)
        self.assertEqual(self.instances[0].otp_metadata_queries, 1)
        self.assertEqual(self.instances[0].otp_queries, 1)

    def test_otp_refresh_fetches_another_current_value(self):
        self.ui = UI(actions=['otp', 'refresh', 'done'])
        self.manager.login(login_request())
        self.assertEqual(self.copies, ['SECRET-ALICE', '000001', '000002'])

    def test_otp_only_account_is_listed_and_copied(self):
        self.factory.passwords = []
        self.manager.login(login_request())
        self.assertEqual(self.copies, ['000001'])
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
        self.assertEqual(self.instances[0].password_queries, 0)
        self.assertEqual(self.instances[0].password_metadata_queries, 1)
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
        self.assertEqual(self.instances[0].password_queries, 1)
        self.assertEqual(self.instances[0].password_metadata_queries, 1)  # initial binding only
        self.assertEqual(self.instances[0].otp_metadata_queries, 0)

    def test_catalog_returns_passwords_before_any_otp_query_and_caches_metadata(self):
        first = self.manager.catalog(catalog_request())
        self.manager.catalog_cache = {
            key: (cached[0] - 60, *cached[1:])
            for key, cached in self.manager.catalog_cache.items()
        }
        second = self.manager.catalog(catalog_request())
        self.assertEqual(first['mode'], 'password')
        self.assertEqual(len(first['accounts']), 2)
        self.assertEqual(first, second)
        self.assertEqual(self.instances[0].password_metadata_queries, 1)
        self.assertEqual(self.instances[0].otp_metadata_queries, 0)
        self.assertNotIn('SECRET-', json.dumps(first))
        self.assertIn('catalog_cache_hit', self.logs)

    def test_catalog_falls_back_to_otp_only_when_no_password_exists(self):
        self.factory.passwords = []
        result = self.manager.catalog(catalog_request())
        cached = self.manager.catalog(catalog_request())
        self.assertEqual(result['mode'], 'otp')
        self.assertEqual(result, cached)
        self.assertEqual(len(result['accounts']), 1)
        self.assertEqual(self.instances[0].password_metadata_queries, 1)
        self.assertEqual(self.instances[0].otp_metadata_queries, 1)
        self.assertNotIn('111111', json.dumps(result))

    def test_alfred_selection_skips_picker_and_metadata_scans(self):
        identity = account_list(PASSWORDS, 'password', 'example.com', metadata_only=True)[1].identity
        result = self.manager.select(select_request(identity))
        self.assertTrue(result['ok'])
        self.assertEqual(self.copies, ['SECRET-BOB'])
        self.assertEqual(self.ui.pickers, [])
        self.assertEqual(self.instances[0].password_queries, 1)
        self.assertEqual(self.instances[0].password_metadata_queries, 0)
        self.assertEqual(self.instances[0].otp_metadata_queries, 0)
        self.assertNotIn('SECRET-', json.dumps(result))

    def test_repeated_password_login_reuses_session_cache_until_refreshed_or_locked(self):
        self.manager.login(login_request())
        self.assertEqual(self.copies, ['SECRET-ALICE'])
        self.assertEqual(self.instances[0].password_queries, 1)
        self.factory.passwords = [dict(PASSWORDS[0], PWD='ROTATED-SECRET'), PASSWORDS[1]]
        self.ui = UI()
        self.manager.login(login_request())
        self.assertEqual(self.copies, ['SECRET-ALICE', 'SECRET-ALICE'])
        self.assertEqual(self.instances[0].password_queries, 1)  # served from session cache without Touch ID read
        self.ui = UI(actions=['refresh_password', 'done'])
        self.manager.login(login_request())
        self.assertEqual(self.copies, ['SECRET-ALICE', 'SECRET-ALICE', 'SECRET-ALICE', 'ROTATED-SECRET'])
        self.assertEqual(self.instances[0].password_queries, 2)

    def test_returning_from_otp_to_password_uses_cached_password_without_reprompt(self):
        self.ui = UI(actions=['otp', 'password', 'done'])
        self.manager.login(login_request())
        self.assertEqual(self.copies, ['SECRET-ALICE', '000001', 'SECRET-ALICE'])
        self.assertEqual(self.instances[0].password_queries, 1)
        self.assertEqual(self.instances[0].otp_queries, 1)

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
        self.assertEqual(self.instances[0].password_queries, 0)
        self.assertEqual(self.instances[0].otp_queries, 0)

    def test_otp_alias_reads_code_once_without_password_scan(self):
        self.factory.passwords = []
        self.manager.login(login_request(save_alias='code'))
        self.assertEqual(self.instances[0].otp_queries, 0)
        self.ui = UI()
        self.manager.login(login_request(domain='', alias='code'))
        self.assertEqual(self.copies, ['000001'])
        self.assertEqual(self.instances[0].otp_queries, 1)

    def test_metadata_can_omit_optional_source_without_losing_binding(self):
        self.factory.passwords = []
        self.factory.codes = [{'username': 'alice', 'domain': 'example.com', 'source': 'Personal', 'code': 'unused'}]
        original = self.factory.entries
        def entries(instance, kind):
            result = original(instance, kind)
            if kind == 'otp_list':
                return [{key: value for key, value in row.items() if key != 'source'} for row in result]
            return result
        self.factory.entries = entries
        self.manager.login(login_request(save_alias='code'))
        self.manager.login(login_request(domain='', alias='code'))
        self.assertEqual(self.copies, ['000001'])

    def test_metadata_never_retains_or_copies_a_code_value(self):
        rows = account_list([{'username': 'alice', 'domain': 'example.com', 'code': 'DO-NOT-RETAIN'}],
                            'otp', 'example.com', metadata_only=True)
        self.assertEqual(rows[0].value, '')
        from passwords_bridge.login_flow import LoginFlow
        flow = LoginFlow(self.manager, self.store, login_request(), self.ui)
        with self.assertRaises(CredentialLookupError): flow.copy(rows[0])
        self.assertEqual(self.copies, [])

    def test_missing_source_does_not_allow_ambiguous_factor_selection(self):
        codes = account_list([{'username': 'alice', 'domain': 'example.com', 'source': 'A', 'code': '111111'},
                              {'username': 'alice', 'domain': 'example.com', 'source': 'B', 'code': '222222'}],
                             'otp', 'example.com')
        identity = {'kind': 'otp', 'username': 'alice', 'sites': ['example.com'], 'title': ''}
        with self.assertRaises(CredentialLookupError): resolve_otp_metadata(codes, identity)

    def test_unsupported_metadata_does_not_eagerly_read_otp(self):
        original = self.factory.entries
        def entries(instance, kind):
            if kind == 'otp_list': raise BridgeError('metadata unavailable')
            return original(instance, kind)
        self.factory.entries = entries
        self.manager.login(login_request())
        self.assertEqual(self.copies, ['SECRET-ALICE'])
        self.assertEqual(self.instances[0].otp_queries, 0)

    def test_unsupported_metadata_falls_back_only_after_otp_click(self):
        original = self.factory.entries
        def entries(instance, kind):
            if kind == 'otp_list': raise BridgeError('metadata unavailable')
            return original(instance, kind)
        self.factory.entries = entries
        self.ui = UI(actions=['otp', 'done'])
        self.manager.login(login_request())
        self.assertEqual(self.copies, ['SECRET-ALICE', '000001'])
        self.assertEqual(self.instances[0].otp_queries, 1)


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

    def test_menu_no_longer_offers_separate_domain_or_original_host_paths(self):
        items = menu('g', self.store)['items']
        self.assertFalse(any('arg' in item for item in items))
        self.assertIn('无需区分', items[0]['subtitle'])

    def test_alias_matches_keep_priority_over_keyword_search(self):
        self.store.put('work', self.binding)
        self.assertEqual(json.loads(menu('work', self.store)['items'][0]['arg']), {'action': 'login', 'alias': 'work'})
        self.assertEqual(json.loads(menu('@work', self.store)['items'][0]['arg']), {'action': 'login', 'alias': 'work'})

    def test_empty_menu_explains_unified_keyword_input(self):
        result = menu('', self.store)
        self.assertIn('直接输入关键词', result['items'][0]['title'])

    def test_catalog_menu_filters_in_alfred_and_carries_metadata_only(self):
        accounts = [{'identity': account_list(PASSWORDS, 'password', 'example.com',
                                               metadata_only=True)[0].identity,
                     'duplicate': False}]
        result = catalog_menu('example.com', accounts, 'ali', 'password')
        action = validate_action(json.loads(result['items'][0]['arg']))
        self.assertEqual(action['action'], 'select')
        self.assertEqual(action['identity']['username'], 'alice')
        self.assertNotIn('SECRET-', json.dumps(result))
        self.assertEqual(json.loads(result['items'][-1]['arg']),
                         {'action': 'search', 'query': 'codes example.com'})
        fallback = catalog_menu('example.com', accounts, mode='otp', fallback=True)
        self.assertEqual(len(fallback['items']), 1)

    def test_performance_log_accepts_only_allowlisted_shape_and_summarizes(self):
        self.assertEqual(format_log_event('perf|catalog_total|123'), 'perf catalog_total 123')
        self.assertIsNone(format_log_event('perf|catalog_total|123 SECRET'))
        self.assertIsNone(format_log_event('example.com'))
        path = Path(self.tmp.name) / 'session.log'
        path.with_name('session.log.1').write_text('0 perf catalog_total 50\n')
        path.write_text('1 perf catalog_total 100\n2 perf catalog_total 200\n'
                        '3 copied_password\n4 perf unknown_phase 999\n')
        rendered = stats_menu(path)
        self.assertIn('平均 117 ms', rendered['items'][0]['title'])
        self.assertNotIn('example.com', json.dumps(rendered))


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
                self.assertEqual(copies, ['SECRET-ALICE', '000001'])
                self.assertNotIn('SECRET-', json.dumps(reply))
                self.assertNotIn('000001', json.dumps(reply))
                self.assertNotIn('SECRET-', (Path(root) / 'session.log').read_text())
            finally:
                try: client.rpc({'op': 'stop'}, Path(root))
                except client.Unavailable: pass
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive())

    def test_catalog_rpc_returns_only_metadata_then_selects_without_picker(self):
        with tempfile.TemporaryDirectory(prefix='pw-catalog-ipc-') as root:
            ready = threading.Event()
            copies, ui = [], UI(actions=['done'])
            thread = threading.Thread(target=broker.serve, kwargs={
                'root': root, 'monitor_factory': Monitor, 'session_factory': CatalogSession,
                'copier': lambda value, delay: copies.append(value), 'ready': ready,
                'ui_factory': lambda manager, flow: ui})
            thread.start()
            try:
                self.assertTrue(ready.wait(5))
                catalog = client.rpc(catalog_request(), Path(root))
                self.assertTrue(catalog['ok'])
                self.assertNotIn('SECRET-', json.dumps(catalog))
                chosen = catalog['accounts'][1]['identity']
                selected = client.rpc(select_request(chosen), Path(root))
                self.assertTrue(selected['ok'])
                self.assertEqual(copies, ['SECRET-BOB'])
                self.assertEqual(ui.pickers, [])
                self.assertNotIn('SECRET-', json.dumps(selected))
                log = (Path(root) / 'session.log').read_text()
                self.assertIn(' perf catalog_total ', log)
                self.assertIn(' perf password_value ', log)
                self.assertNotIn('example.com', log)
                self.assertNotIn('alice', log)
                self.assertNotIn('bob', log)
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
