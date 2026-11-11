import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("apple_passwords", ROOT / "src/apple-passwords.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)
from passwords_bridge import clipboard, extension, session
from passwords_bridge.browser import BridgeError


class QueryTests(unittest.TestCase):
    def test_url_and_literal_username(self):
        self.assertEqual(app.parse_query("https://GitHub.com/login?a=b Jane Doe"), ("github.com", "Jane Doe"))
        self.assertEqual(app.parse_query("例子.测试 me@example.com"), ("xn--fsqu00a.xn--0zwm56d", "me@example.com"))
        self.assertEqual(app.parse_query("example.com --show $(echo literal)"), ("example.com", "--show $(echo literal)"))

    def test_bad_sites_fail_before_password_access(self):
        for query in ("", "--show", "https://user:secret@example.com", "file:///etc/passwd",
                      "https://", "example..com", "$(touch-x).com", "[broken"):
            with self.subTest(query=query), self.assertRaises(BridgeError):
                app.parse_query(query)

    def test_clear_delay_is_bounded(self):
        for value in ("-1", "NaN", "1.5", "90000"):
            with patch.dict(os.environ, {"CLEAR_CLIPBOARD_AFTER": value}), self.assertRaises(BridgeError):
                app.clear_seconds()

    def test_direct_catalog_syntax_keeps_short_names_as_visible_completions(self):
        self.assertIsNone(app.catalog_query('github'))
        self.assertEqual(app.catalog_query('github.com ali'), ('github.com', 'ali', 'password'))
        self.assertEqual(app.catalog_query('codes github.com'), ('github.com', '', 'otp'))
        self.assertEqual(app.catalog_query('site intranet bob'), ('intranet', 'bob', 'password'))


class FrontendTests(unittest.TestCase):
    def test_no_secret_in_notification(self):
        for mode in ("password", "otp"):
            with patch.object(app, "request_copy", return_value={'ok': True, 'message': 'copied'}) as request, \
                 patch.dict(os.environ, {"CLEAR_CLIPBOARD_AFTER": "45", 'SESSION_IDLE_SECONDS': '300'}):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    status = app.main([mode, "example.com --show literal user"])
            self.assertEqual(status, 0)
            request.assert_called_once_with(mode, "example.com", "--show literal user", 45, 300)
            self.assertNotIn("FAKE-SECRET", output.getvalue())

    def test_unexpected_exception_text_is_not_forwarded(self):
        for error in (ValueError("FAKE-SECRET"), RuntimeError("FAKE-SECRET")):
            with patch.object(app, "request_copy", side_effect=error):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    status = app.main(["password", "example.com"])
            self.assertEqual(status, 1)
            self.assertNotIn("FAKE-SECRET", output.getvalue())

    def test_setup_does_not_pair_or_read_credentials(self):
        with patch.object(app, "setup") as prepare, patch.object(app, "request_copy") as read:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(app.main(["pair"]), 0)
            prepare.assert_called_once()
            read.assert_not_called()

    def test_invalid_mode_never_starts_session(self):
        with patch.object(app, "request_copy") as read, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(app.main(["save", "example.com"]), 1)
            read.assert_not_called()

    def test_manual_lock_does_not_start_or_read_a_session(self):
        with patch.object(app, 'lock_session', return_value={'ok': True, 'message': 'locked'}) as lock, \
             patch.object(app, 'request_copy') as copy, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(app.main(['lock']), 0)
            lock.assert_called_once()
            copy.assert_not_called()

    def test_direct_menu_returns_secret_free_alfred_results(self):
        identity = {'kind': 'password', 'username': 'alice', 'title': 'Personal',
                    'sites': ['example.com']}
        reply = {'ok': True, 'mode': 'password', 'fallback': False,
                 'accounts': [{'identity': identity, 'duplicate': False}]}
        with tempfile.TemporaryDirectory() as root, \
             patch.object(app, 'request_catalog', return_value=reply) as request, \
             patch.dict(os.environ, {'SESSION_IDLE_SECONDS': '300'}):
            result = app.direct_menu('example.com ali', app.AliasStore(root))
        request.assert_called_once_with('example.com', 'password', 300)
        self.assertEqual(json.loads(result['items'][0]['arg'])['action'], 'select')
        self.assertNotIn('SECRET-', json.dumps(result))

    def test_exact_alias_wins_over_direct_localhost_routing(self):
        identity = {'kind': 'password', 'username': 'alice', 'title': 'Local',
                    'sites': ['localhost']}
        with tempfile.TemporaryDirectory() as root:
            store = app.AliasStore(root)
            store.put('localhost', {'domain': 'localhost', 'identity': identity})
            with patch.object(app, 'request_catalog') as request:
                result = app.direct_menu('localhost', store)
        self.assertEqual(json.loads(result['items'][0]['arg']),
                         {'action': 'login', 'alias': 'localhost'})
        request.assert_not_called()

    def test_selected_alfred_result_goes_directly_to_copy_request(self):
        identity = {'kind': 'password', 'username': 'alice', 'title': 'Personal',
                    'sites': ['example.com']}
        action = {'action': 'select', 'domain': 'example.com', 'identity': identity}
        with patch.object(app, 'request_selected', return_value={'ok': True, 'message': 'copied'}) as selected, \
             patch.dict(os.environ, {'CLEAR_CLIPBOARD_AFTER': '45', 'SESSION_IDLE_SECONDS': '300',
                                     'SHOW_SECRETS': '1', 'DISPLAY_SECONDS': '45'}):
            self.assertEqual(app.interactive(action), (True, 'copied'))
        selected.assert_called_once_with('example.com', identity, 45, 300, True, 45)

    def test_idle_configuration_rejects_unbounded_values(self):
        for value in ('0', '-1', '1801', 'forever'):
            with patch.dict(os.environ, {'SESSION_IDLE_SECONDS': value}), self.assertRaises(BridgeError):
                app.idle_seconds()

    def test_context_closes_before_secret_returns(self):
        events = []
        with patch.object(session, "PasswordSession") as constructor:
            owned = constructor.return_value
            owned.__enter__.return_value = owned
            owned.read.side_effect = lambda mode: events.append("read") or "fixture"
            owned.__exit__.side_effect = lambda *args: events.append("closed") or False
            self.assertEqual(session.read_secret("password", "example.com", "user"), "fixture")
            events.append("returned")
        self.assertEqual(events, ["read", "closed", "returned"])

    def test_pairing_failure_still_closes_session(self):
        with patch.object(session, "PasswordSession") as constructor:
            owned = constructor.return_value
            owned.__enter__.return_value = owned
            owned.pair.side_effect = BridgeError("cancelled")
            with self.assertRaises(BridgeError):
                session.read_secret("otp", "example.com", "user")
            owned.__exit__.assert_called_once()
            owned.read.assert_not_called()


class ResultTests(unittest.TestCase):
    def test_metadata_listing_uses_native_ghost_search(self):
        owned = session.PasswordSession('example.com', 'alice', 'otp_list')
        with patch.object(owned, 'request', return_value={'data': {'STATUS': 0, 'Entries': []}}) as sender:
            self.assertEqual(owned.entries('otp_list'), [])
        request = sender.call_args.args[0]
        self.assertEqual(request['cmd'], 16)
        self.assertEqual(request['body']['ACT'], 5)
        self.assertEqual(request['body']['frameURLs'], ['https://example.com'])

    def test_password_metadata_listing_uses_login_names_ghost_search(self):
        owned = session.PasswordSession('example.com', 'alice', 'password_list')
        with patch.object(owned, 'request', return_value={'data': {'STATUS': 0, 'Entries': []}}) as sender:
            self.assertEqual(owned.entries('password_list'), [])
        request = sender.call_args.args[0]
        self.assertEqual(request['cmd'], 4)
        self.assertEqual(request['qid'], 'CmdGetLoginNames4URL')
        self.assertEqual(request['body'], {'ACT': 5, 'URL': 'example.com'})

    def test_password_formats_and_exact_username(self):
        rows = [{"USR": "alice", "PWD": "FAKE-A"}, {"USR": "bob", "PWD": "FAKE-B"}]
        self.assertEqual(session.select_secret({"data": {"STATUS": 0, "Entries": rows}}, "password", "bob"), "FAKE-B")
        self.assertEqual(session.select_secret({"data": {"STATUS": 0, "Entry_0": rows[0]}}, "password", ""), "FAKE-A")

    def test_otp_preserves_leading_zero(self):
        data = {"data": {"STATUS": 0, "Entries": [{"username": "alice", "code": "012345"}]}}
        self.assertEqual(session.select_secret(data, "otp", "alice"), "012345")

    def test_ambiguous_missing_and_invalid_results_fail_closed(self):
        for data in ({"STATUS": 0, "Entries": [{"USR": "a", "PWD": "one"}, {"USR": "b", "PWD": "two"}]},
                     {"STATUS": 0, "Entries": []}, {"STATUS": 0, "Entries": [{"PWD": "Not Included"}]},
                     {"STATUS": 3}, {"STATUS": 9}, {"STATUS": False}, {}):
            with self.subTest(data=data), self.assertRaises(BridgeError):
                session.select_secret({"data": data}, "password", "")

    def test_error_payload_is_not_exposed(self):
        with self.assertRaises(BridgeError) as captured:
            session.select_secret({"status": 100, "error": "FAKE-SECRET"}, "password", "")
        self.assertNotIn("FAKE-SECRET", str(captured.exception))


class ClipboardTests(unittest.TestCase):
    def test_secret_only_on_stdin_and_timer_holds_only_counter(self):
        result = subprocess.CompletedProcess([], 0, b"42\n", b"")
        with patch.object(clipboard.subprocess, "run", return_value=result) as writer, \
             patch.object(clipboard.subprocess, "Popen") as timer:
            clipboard.copy_secret("FAKE-SECRET", 45)
        self.assertEqual(writer.call_args.kwargs["input"], b"FAKE-SECRET")
        self.assertNotIn("FAKE-SECRET", repr(writer.call_args.args))
        self.assertNotIn("FAKE-SECRET", repr(timer.call_args))
        self.assertIn("42", timer.call_args.args[0])
        self.assertEqual(timer.call_args.kwargs["stdin"], subprocess.DEVNULL)

    def test_disabled_timer_spawns_no_process(self):
        with patch.object(clipboard.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"1", b"")), \
             patch.object(clipboard.subprocess, "Popen") as timer:
            clipboard.copy_secret("fixture", 0)
            timer.assert_not_called()

    def test_clipboard_failure_is_not_reported_as_success(self):
        with patch.object(clipboard.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, b"FAKE-SECRET", b"")), \
             patch.object(clipboard.subprocess, "Popen") as timer, self.assertRaises(BridgeError):
            clipboard.copy_secret("fixture", 45)
        timer.assert_not_called()


class ExtensionTests(unittest.TestCase):
    def test_exact_digest_required(self):
        fixture = b"reviewed extension fixture"
        with patch.object(extension, "SHA256", hashlib.sha256(fixture).hexdigest()):
            self.assertEqual(extension.verify(fixture), fixture)
            with self.assertRaises(BridgeError):
                extension.verify(fixture + b"modified")

    def test_missing_cache_does_not_auto_download_during_copy(self):
        with patch.object(extension, "archive_path", return_value=Path("/missing-extension-fixture.crx")), \
             patch.object(extension.urllib.request, "urlopen") as network, self.assertRaises(BridgeError):
            extension.prepare(Path("unused"))
        network.assert_not_called()


@unittest.skipUnless(sys.platform == "darwin", "macOS pasteboard required")
class NativeClipboardTests(unittest.TestCase):
    def test_native_markers_and_copy_generation_guard(self):
        result = subprocess.run(["/usr/bin/osascript", "-l", "JavaScript",
                                 str(ROOT / "src/secure-clipboard.js"), "--selftest"],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("selftest passed", result.stdout)


@unittest.skipUnless(shutil.which("node"), "Node required only for JS development tests")
class JavaScriptPolicyTests(unittest.TestCase):
    def test_read_only_scope_and_crypto_routing(self):
        result = subprocess.run([shutil.which("node"), str(ROOT / "tests/bridge_policy.test.js")],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Bridge policy tests passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
