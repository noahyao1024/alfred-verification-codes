"""Minimal synchronous Chrome DevTools transport over inherited anonymous pipes.

Chrome owns descriptors 3 (commands) and 4 (replies). No TCP/WebSocket endpoint
is created. Only the exact child process started here is ever shut down.
"""

import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time


class BridgeError(Exception):
    """A public, static error safe to display; never include a CDP payload."""


def browser_binary():
    for relative in (
        "Google Chrome.app/Contents/MacOS/Google Chrome",
        "Brave Browser.app/Contents/MacOS/Brave Browser",
        "Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "Chromium.app/Contents/MacOS/Chromium",
    ):
        for base in (Path("/Applications"), Path.home() / "Applications"):
            binary = base / relative
            if binary.is_file():
                return binary
    raise BridgeError("需要安装 Chrome、Brave、Edge 或 Chromium。")


class PipeBrowser:
    def __init__(self, profile, binary=None):
        self.profile = Path(profile)
        self.binary = Path(binary) if binary else browser_binary()
        self.proc = None
        self.read_fd = self.write_fd = None
        self.buffer = b""
        self.sequence = 0

    def __enter__(self):
        self.profile.mkdir(mode=0o700, parents=True, exist_ok=True)
        command_read, command_write = os.pipe()
        reply_read, reply_write = os.pipe()
        argv = [str(self.binary), "--remote-debugging-pipe",
                "--enable-unsafe-extension-debugging", "--headless=new",
                "--user-data-dir=" + str(self.profile), "--no-first-run",
                "--no-default-browser-check", "--disable-background-networking",
                "--disable-sync", "--disable-component-update", "--disable-default-apps",
                "--disable-background-timer-throttling", "--disable-backgrounding-occluded-windows"]
        try:
            # A tiny exec launcher maps FDs after Popen has closed other handles.
            # It avoids preexec_fn and inherits neither a password nor a PIN.
            self.proc = subprocess.Popen(
                [sys.executable, "-I", str(Path(__file__).resolve()), "--child",
                 str(command_read), str(reply_write)] + argv,
                pass_fds=(command_read, reply_write), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            os.close(command_write)
            os.close(reply_read)
            raise BridgeError("无法启动独立浏览器进程。") from None
        finally:
            os.close(command_read)
            os.close(reply_write)
        self.read_fd, self.write_fd = reply_read, command_write
        try:
            self.send("Browser.getVersion", timeout=20)
            return self
        except BaseException:
            self.close()
            raise

    def send(self, method, params=None, session=None, timeout=30):
        self.sequence += 1
        message = {"id": self.sequence, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        packet = json.dumps(message).encode("utf-8") + b"\0"
        try:
            while packet:
                written = os.write(self.write_fd, packet)
                packet = packet[written:]
        except (BrokenPipeError, OSError):
            raise BridgeError("浏览器连接已关闭。") from None
        deadline = time.monotonic() + timeout
        while True:
            if b"\0" in self.buffer:
                raw, self.buffer = self.buffer.split(b"\0", 1)
                if not raw:
                    continue
                try:
                    reply = json.loads(raw)
                except (ValueError, UnicodeError):
                    raise BridgeError("浏览器响应格式无效。") from None
                if reply.get("id") != self.sequence:
                    continue  # events carry no credentials and need no logging
                if "error" in reply:
                    raise BridgeError("浏览器拒绝操作；当前版本可能不支持该接口。")
                return reply.get("result", {})
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.read_fd], [], [], remaining)[0]:
                raise BridgeError("等待浏览器响应超时。")
            chunk = os.read(self.read_fd, 65536)
            if not chunk:
                raise BridgeError("浏览器连接已关闭。")
            self.buffer += chunk
            if len(self.buffer) > 8 * 1024 * 1024:
                raise BridgeError("浏览器响应超过大小限制。")

    def evaluate(self, expression, session, timeout=30):
        result = self.send("Runtime.evaluate", {
            "expression": expression, "awaitPromise": True, "returnByValue": True,
        }, session=session, timeout=timeout)
        if result.get("exceptionDetails"):
            raise BridgeError("密码扩展执行失败；未输出原始响应。")
        return result.get("result", {}).get("value")

    def close(self):
        try:
            if self.proc is not None and self.proc.poll() is None:
                try:
                    self.send("Browser.close", timeout=2)
                except (BridgeError, OSError):
                    pass
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proc.terminate()
                    try:
                        self.proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.proc.kill()
                        self.proc.wait(timeout=5)
        finally:
            for fd in (self.read_fd, self.write_fd):
                if fd is not None:
                    os.close(fd)
            self.read_fd = self.write_fd = None
            self.buffer = b""

    def __exit__(self, *exc):
        self.close()


def child_exec(args):
    # Duplicate both sources first: mapping one to 3/4 must not overwrite the other.
    source_read, source_write = os.dup(int(args[0])), os.dup(int(args[1]))
    os.dup2(source_read, 3, inheritable=True)
    os.dup2(source_write, 4, inheritable=True)
    # Python 3.9/macOS may preserve CLOEXEC when dup2's source equals its target.
    os.set_inheritable(3, True)
    os.set_inheritable(4, True)
    for fd in set([source_read, source_write, int(args[0]), int(args[1])]) - {3, 4}:
        os.close(fd)
    os.execv(args[2], args[2:])


if __name__ == "__main__" and sys.argv[1:2] == ["--child"]:
    child_exec(sys.argv[2:])
