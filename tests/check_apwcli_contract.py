"""Run with apwcli's Python: verifies the real CLI with a fake vault and clipboard.

No daemon, browser, pairing, or real clipboard is used by these tests.
"""

import subprocess
import unittest
from unittest.mock import patch

from apwcli.cli.common import app, client
from apwlib import OTPEntry, PasswordEntry
from typer.testing import CliRunner


class InstalledCLIContract(unittest.TestCase):
    def test_password_argv_and_copy_only_output(self):
        username = "--show literal user"
        secret = "FAKE-PASSWORD-only-\u4e2d\u6587"
        with patch.object(client, "get_password", return_value=[PasswordEntry(username, "example.com", secret)]) as read, \
             patch("apwcli.cli.common.subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as copy:
            result = CliRunner().invoke(app, ["pw", "get", "--clipboard", "--clear-after", "0",
                                             "--", "example.com", username])
        self.assertEqual(result.exit_code, 0, result.output)
        read.assert_called_once_with("example.com", username)
        copy.assert_called_once_with(["pbcopy"], input=secret.encode(), check=True)
        self.assertNotIn(secret, result.output)

    def test_otp_filters_exact_username_and_copies_fresh_code(self):
        with patch.object(client, "get_otp", return_value=[OTPEntry("alice", "example.com", "012345"),
                                                         OTPEntry("bob", "example.com", "987654")]), \
             patch("apwcli.cli.common.subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as copy:
            result = CliRunner().invoke(app, ["otp", "get", "--clipboard", "--clear-after", "0",
                                             "--", "example.com", "alice"])
        self.assertEqual(result.exit_code, 0, result.output)
        copy.assert_called_once_with(["pbcopy"], input=b"012345", check=True)
        self.assertNotIn("012345", result.output)
        self.assertNotIn("987654", result.output)

    def test_multiple_and_empty_matches_never_write_clipboard(self):
        for group, method, entries in (
            ("pw", "get_password", []),
            ("pw", "get_password", [PasswordEntry("a", "example.com", "FAKE-one"),
                                      PasswordEntry("b", "example.com", "FAKE-two")]),
            ("otp", "get_otp", []),
            ("otp", "get_otp", [OTPEntry("a", "example.com", "123456"),
                                 OTPEntry("b", "example.com", "654321")]),
        ):
            with self.subTest(group=group, count=len(entries)), \
                 patch.object(client, method, return_value=entries), \
                 patch("apwcli.cli.common.subprocess.run") as copy:
                result = CliRunner().invoke(app, [group, "get", "--clipboard", "--clear-after", "0",
                                                 "--", "example.com"])
                self.assertIn(result.exit_code, (2, 3))
                copy.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
