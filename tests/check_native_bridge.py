"""Explicit macOS integration checks: no PIN prompt and no real credential reads.

Prepare the pinned archive with pwsetup first, then run with /usr/bin/python3.
"""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from passwords_bridge.browser import PipeBrowser
from passwords_bridge.session import PasswordSession


class NativeBridgeTests(unittest.TestCase):
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
