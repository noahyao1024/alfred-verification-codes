import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("apple_passwords", ROOT / "src/apple-passwords.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class QueryTests(unittest.TestCase):
    def test_url_reduces_to_hostname_and_preserves_username(self):
        self.assertEqual(app.parse_query("https://GitHub.com/login?a=b Jane Doe"),
                         ("github.com", "Jane Doe"))
        self.assertEqual(app.parse_query("例子.测试 me@example.com"),
                         ("xn--fsqu00a.xn--0zwm56d", "me@example.com"))

    def test_rejects_options_credentials_and_non_web_urls(self):
        for query in ("", "--show", "https://user:secret@example.com", "file:///etc/passwd",
                      "https://", "example..com", "$(touch-x).com", "[broken"):
            with self.subTest(query=query), self.assertRaises(ValueError):
                app.parse_query(query)

    def test_invalid_clear_delay_fails_before_copy(self):
        for value in ("-1", "NaN", "1.5", "90000"):
            with self.subTest(value=value), patch.dict(os.environ, {"CLEAR_CLIPBOARD_AFTER": value}):
                with self.assertRaises(ValueError):
                    app.clear_seconds()

    def test_explicit_missing_binary_does_not_silently_fallback(self):
        with patch.dict(os.environ, {"APWCLI_PATH": "/does-not-exist/apwcli"}):
            with self.assertRaises(ValueError):
                app.cli_path()


class CopyTests(unittest.TestCase):
    def invoke(self, mode, query, code=0, stdout=b"", stderr=b""):
        result = subprocess.CompletedProcess([], code, stdout, stderr)
        with patch.object(app, "cli_path", return_value="/fake/apwcli"), \
             patch.object(app.subprocess, "run", return_value=result) as proc, \
             patch.dict(os.environ, {"CLEAR_CLIPBOARD_AFTER": "45"}):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = app.main([mode, query])
            return status, output.getvalue(), proc.call_args

    def test_password_and_otp_use_clipboard_mode_and_literal_argv(self):
        username = "--show $(touch /tmp/NEVER) `text` 中文"
        for mode, group in (("password", "pw"), ("otp", "otp")):
            status, output, call = self.invoke(mode, "example.com " + username)
            self.assertEqual(status, 0)
            self.assertEqual(call.args[0], ["/fake/apwcli", group, "get", "--clipboard",
                                          "--clear-after", "45", "--", "example.com", username])
            self.assertEqual(call.kwargs["stdin"], subprocess.DEVNULL)
            self.assertNotIn("shell", call.kwargs)
            self.assertTrue(call.kwargs["env"]["PATH"].startswith(str(ROOT / "src/clipboard-bin") + ":"))
            self.assertNotIn(username, output)

    def test_backend_output_is_never_forwarded_even_on_failure(self):
        secret = b"FAKE-SECRET-DO-NOT-LOG"
        for code in (0, 1, 9, 127):
            status, output, _ = self.invoke("password", "example.com", code, secret, secret)
            self.assertNotIn(secret.decode(), output)
            self.assertEqual(status, 0 if code == 0 else 1)

    def test_ambiguous_and_empty_results_do_not_claim_success(self):
        for error in (b"multiple matches (alice, bob)", b"no password to copy",
                      b"no one-time code to copy"):
            status, output, _ = self.invoke("otp", "example.com", 2, stderr=error)
            self.assertEqual(status, 1)
            self.assertNotIn("已复制", output)
            self.assertNotIn("alice", output)

    def test_pairing_has_no_password_read(self):
        status, _, call = self.invoke("pair", "")
        self.assertEqual(status, 0)
        self.assertEqual(call.args[0], ["/fake/apwcli", "daemon", "pair"])

    def test_timeout_does_not_expose_captured_output(self):
        timeout = subprocess.TimeoutExpired("fake", 180, output=b"FAKE-SECRET")
        with patch.object(app, "cli_path", return_value="/fake/apwcli"), \
             patch.object(app.subprocess, "run", side_effect=timeout):
            ok, message = app.run("password", "example.com")
        self.assertFalse(ok)
        self.assertIn("超时", message)
        self.assertNotIn("FAKE-SECRET", message)

    def test_input_errors_never_launch_backend(self):
        with patch.object(app.subprocess, "run") as proc, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(app.main(["password", "--show"]), 1)
            self.assertEqual(app.main(["invalid-mode", "example.com"]), 1)
            proc.assert_not_called()


@unittest.skipUnless(sys.platform == "darwin", "macOS pasteboard required")
class NativeClipboardTests(unittest.TestCase):
    def test_private_pasteboard_preserves_text_and_privacy_markers(self):
        result = subprocess.run(["/usr/bin/osascript", "-l", "JavaScript",
                                 str(ROOT / "src/secure-clipboard.js"), "--selftest"],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("selftest passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
