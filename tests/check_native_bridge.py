"""Explicit macOS integration checks: no PIN prompt and no real credential reads.

Prepare the pinned archive with pwsetup first, then run with /usr/bin/python3.
"""

import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from passwords_bridge.browser import PipeBrowser
from passwords_bridge.session import PasswordSession
from passwords_bridge.screen import LockMonitor
from passwords_bridge.broker import SessionManager, SessionExpired


def notify_private(name):
    source = 'ObjC.import("AppKit"); $.NSDistributedNotificationCenter.defaultCenter.postNotificationNameObjectUserInfoDeliverImmediately($("' + name + '"),$(),$(),true);'
    subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', '-e', source], check=True, capture_output=True)


class SyntheticCredentials(PasswordSession):
    """Real browser/extension; synthetic auth/data, never calls the Apple helper."""
    paired_fixture = False
    pair_count = 0
    block_read = False
    def state(self):
        super().state()  # exercise the live pipe/worker, without authorizing it
        return {'paired': self.paired_fixture}
    def pair(self):
        self.pair_count += 1
        self.paired_fixture = True
    def read(self, mode):
        if self.block_read:
            self.evaluate('new Promise(function () {})')
        return 'SYNTHETIC-CREDENTIAL'


def fixture_request(mode='password'):
    return {'op': 'copy', 'mode': mode, 'domain': 'example.invalid', 'username': 'fixture-user',
            'clear_after': 0, 'idle_seconds': 300}


class NativeReuseTests(unittest.TestCase):
    def setUp(self):
        self.name = 'com.noah.passwords.test.' + uuid.uuid4().hex
        self.monitor = LockMonitor(self.name)
        # The test must work even while the owner is away/locked. Only the snapshot
        # is simulated; real notifications remain active and all credentials are fake.
        self.monitor.snapshot = type('FixtureConsole', (), {'blocked': lambda self: False})()
        self.copies = []
        self.manager = SessionManager(self.monitor, SyntheticCredentials,
                                      lambda value, delay: self.copies.append(value))
    def tearDown(self):
        self.manager.close_session()
        self.monitor.close()

    def test_same_browser_is_reused_then_native_notification_closes_it(self):
        self.manager.copy(fixture_request())
        owned = self.manager.session
        child, directory = owned.browser.proc, Path(owned.temporary.name)
        self.manager.copy(fixture_request('otp'))
        self.assertIs(self.manager.session, owned)
        self.assertEqual(owned.pair_count, 1)
        self.assertEqual(len(self.copies), 2)
        listeners = subprocess.run(['/usr/sbin/lsof', '-nP', '-a', '-p', str(child.pid),
                                    '-iTCP', '-sTCP:LISTEN'], capture_output=True)
        self.assertEqual(listeners.returncode, 1)
        self.assertFalse(listeners.stdout)
        notify_private(self.name)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try: self.manager.tick()
            except SessionExpired: break
            time.sleep(.05)
        self.assertTrue(self.manager.expired)
        self.assertIsNotNone(child.poll())
        self.assertFalse(directory.exists())

    def test_native_notification_interrupts_a_pending_browser_read(self):
        self.manager.copy(fixture_request())
        owned = self.manager.session
        child = owned.browser.proc
        owned.block_read = True
        self.copies.clear()
        timer = threading.Timer(.25, notify_private, args=(self.name,))
        timer.start()
        try:
            with self.assertRaises(SessionExpired): self.manager.copy(fixture_request('otp'))
        finally:
            timer.join(timeout=3)
        self.assertFalse(timer.is_alive())
        self.assertEqual(self.copies, [])
        self.assertIsNotNone(child.poll())


class NativeBridgeTests(unittest.TestCase):
    def test_metadata_scope_is_read_only_and_still_requires_pairing(self):
        with PasswordSession('example.invalid', 'fixture-user', 'otp_list') as session:
            metadata = {'cmd': 16, 'qid': 'CmdDidFillOneTimeCode',
                        'body': {'ACT': 5, 'TYPE': 'oneTimeCodes', 'frameURLs': ['https://example.invalid']}}
            self.assertEqual(session.request(metadata)['status'], 9)
            metadata['cmd'], metadata['body']['ACT'] = 17, 2
            self.assertEqual(session.request(metadata)['status'], 2)
        with PasswordSession('example.invalid', 'fixture-user', 'password_list') as session:
            pw_meta = {'cmd': 4, 'qid': 'CmdGetLoginNames4URL', 'url': 'example.invalid',
                       'body': {'ACT': 5, 'URL': 'example.invalid'}}
            self.assertEqual(session.request(pw_meta)['status'], 9)
            pw_read = {'cmd': 5, 'qid': 'CmdGetPassword4LoginName', 'url': 'example.invalid',
                       'body': {'ACT': 2, 'URL': 'example.invalid', 'USR': 'fixture-user'}}
            self.assertEqual(session.request(pw_read)['status'], 2)

    def test_account_picker_modal_search_and_selection(self):
        script = Path(__file__).resolve().parents[1] / 'src/passwords_bridge/local_ui.js'
        result = subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', str(script), '--smoke'],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'action': 'select', 'index': 1})

    def test_pinned_extension_read_scope_and_teardown(self):
        with PasswordSession("example.invalid", "fixture-user", "password") as session:
            child = session.browser.proc
            root = Path(session.temporary.name)
            self.assertFalse(session.state()["paired"])
            listener = subprocess.run(["/usr/sbin/lsof", "-nP", "-a", "-p", str(child.pid),
                                       "-iTCP", "-sTCP:LISTEN"], capture_output=True)
            self.assertEqual(listener.returncode, 1, listener.stderr.decode())
            self.assertFalse(listener.stdout)
            self.assertEqual(session.request({"cmd": 6, "qid": "write", "body": {}})["status"], 2)
            wrong = {"cmd": 5, "qid": "CmdGetPassword4LoginName",
                     "body": {"ACT": 2, "URL": "other.invalid", "USR": "fixture-user"}}
            self.assertEqual(session.request(wrong)["status"], 2)
            wrong["body"]["URL"] = "example.invalid"
            # This is rejected by the bridge before reaching the native helper.
            self.assertEqual(session.request(wrong)["status"], 9)
        self.assertIsNotNone(child.poll())
        self.assertFalse(root.exists())

    def test_failure_closes_browser_and_removes_profile(self):
        with self.assertRaisesRegex(RuntimeError, "synthetic"):
            with PasswordSession("example.invalid") as session:
                child, root = session.browser.proc, Path(session.temporary.name)
                raise RuntimeError("synthetic failure")
        self.assertIsNotNone(child.poll())
        self.assertFalse(root.exists())

    def test_parent_pipe_eof_exits_browser(self):
        with tempfile.TemporaryDirectory(prefix="pw-eof-test-") as directory:
            browser = PipeBrowser(directory)
            browser.__enter__()
            try:
                os.close(browser.write_fd)
                browser.write_fd = None
                browser.proc.wait(timeout=5)
                self.assertIsNotNone(browser.proc.poll())
            finally:
                if browser.proc.poll() is None:
                    browser.proc.terminate()
                    browser.proc.wait(timeout=5)
                os.close(browser.read_fd)
                browser.read_fd = None


if __name__ == "__main__":
    unittest.main(verbosity=2)
