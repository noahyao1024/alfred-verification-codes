"""An owned browser session with a scope that is cleared between reads."""

import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

from .browser import BridgeError, PipeBrowser
from .extension import EXTENSION_ID, NATIVE_MANIFEST, data_dir, prepare


class PasswordSession:
    def __init__(self, domain, username="", mode="password", guard=None):
        self.domain, self.username = domain, username
        if mode not in ("password", "otp"):
            raise BridgeError("不支持的读取类型。")
        self.mode = mode
        self.guard = guard
        self.temporary = self.browser = self.worker = None

    def __enter__(self):
        if not NATIVE_MANIFEST.is_file():
            raise BridgeError("未找到 Apple Passwords 浏览器 helper，请先配置官方 iCloud Passwords 扩展。")
        self.temporary = tempfile.TemporaryDirectory(prefix="session-", dir=str(data_dir()))
        try:
            root = Path(self.temporary.name)
            extension = root / "extension"
            prepare(extension)
            profile = root / "browser"
            hosts = profile / "NativeMessagingHosts"
            hosts.mkdir(mode=0o700, parents=True)
            shutil.copyfile(NATIVE_MANIFEST, hosts / "com.apple.passwordmanager.json")
            self.browser = PipeBrowser(profile, guard=self.guard)
            self.browser.__enter__()
            result = self.browser.send("Extensions.loadUnpacked", {"path": str(extension)})
            if result.get("id") != EXTENSION_ID:
                raise BridgeError("加载的扩展 ID 不匹配，已停止。")
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                targets = self.browser.send("Target.getTargets").get("targetInfos", [])
                target = next((t for t in targets if t.get("type") == "service_worker" and
                               t.get("url") == "chrome-extension://" + EXTENSION_ID + "/background.js"), None)
                if target:
                    self.worker = self.browser.send("Target.attachToTarget", {
                        "targetId": target["targetId"], "flatten": True,
                    })["sessionId"]
                    break
                time.sleep(0.1)
            if not self.worker:
                raise BridgeError("密码扩展没有启动。")
            while time.monotonic() < deadline:
                if self.evaluate("typeof globalThis.__passwordsBridge === 'object'"):
                    self.set_scope(self.domain, self.username, self.mode)
                    return self
                time.sleep(0.1)
            raise BridgeError("密码扩展桥接初始化超时。")
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def evaluate(self, expression):
        return self.browser.evaluate(expression, self.worker)

    def state(self):
        # A wedged keepalive must not hold up the broker's control endpoint for 30s.
        return self.browser.evaluate("globalThis.__passwordsBridge.state()", self.worker, timeout=3)

    def set_scope(self, domain, username, mode):
        if mode not in ('password', 'otp'):
            raise BridgeError('不支持的读取类型。')
        self.domain, self.username, self.mode = domain, username, mode
        scope = json.dumps({'domain': domain, 'username': username, 'operation': mode})
        self.evaluate('Object.assign(globalThis.__passwordsConfig,' + scope + '); true')

    def clear_scope(self):
        self.evaluate('Object.assign(globalThis.__passwordsConfig,{domain:"",username:"",operation:""}); true')
        self.domain = self.username = ''

    def request(self, message):
        return self.evaluate("globalThis.__passwordsBridge.request(" + json.dumps(message) + ")")

    def wait_state(self, desired, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.state().get("state")
            if state == desired:
                return
            if desired == "SessionKeySet" and state == "NotInSession":
                raise BridgeError("Apple 配对失败或已取消，请重新运行复制命令。")
            time.sleep(0.2)
        raise BridgeError("Apple 配对等待超时。")

    def pair(self):
        if self.request({"cmd": 2}).get("status") != 0:
            raise BridgeError("无法请求 Apple 配对。")
        self.wait_state("MSG1Set")
        pin = self.prompt_pin()
        try:
            if self.request({"cmd": 2, "pin": pin}).get("status") != 0:
                raise BridgeError("Apple 配对失败。")
            self.wait_state("SessionKeySet")
        finally:
            pin = None

    def prompt_pin(self):
        script = '''set answer to display dialog "输入 Apple 系统窗口显示的六位配对码。会话将在空闲超时、锁屏或手动锁定后关闭。" default answer "" with hidden answer with title "Passwords Quick Copy" buttons {"取消", "继续"} default button "继续" cancel button "取消" giving up after 90
if gave up of answer then return ""
return text returned of answer'''
        dialog = subprocess.Popen(["/usr/bin/osascript", "-e", script], stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 95
            while dialog.poll() is None:
                if time.monotonic() > deadline:
                    raise BridgeError("配对输入超时。")
                self.state()  # keep the worker alive only during this bounded operation
                time.sleep(0.5)
            value = dialog.communicate()[0].decode("utf-8").strip()
            if dialog.returncode or not re.fullmatch(r"[0-9]{6}", value):
                raise BridgeError("配对已取消，或输入的配对码不是六位数字。")
            return value
        finally:
            if dialog.poll() is None:
                dialog.terminate()
                dialog.wait(timeout=5)
            dialog.stdout.close()

    def read(self, mode):
        if mode == "password":
            message = {"cmd": 5, "qid": "CmdGetPassword4LoginName", "tabId": 0, "frameId": 0,
                       "url": self.domain,
                       "body": {"ACT": 2, "URL": self.domain, "USR": self.username}}
        elif mode == "otp":
            message = {"cmd": 17, "qid": "CmdDidFillOneTimeCode", "tabId": 0, "frameId": 0,
                       "body": {"ACT": 2, "TYPE": "oneTimeCodes", "frameURLs": ["https://" + self.domain]}}
        else:
            raise BridgeError("不支持的读取类型。")
        return select_secret(self.request(message), mode, self.username)

    def __exit__(self, *exc):
        try:
            if self.browser:
                self.browser.close()
        finally:
            if self.temporary:
                self.temporary.cleanup()


class CredentialLookupError(BridgeError):
    """A valid paired session may be retained after an empty/ambiguous lookup."""


def select_secret(response, mode, username):
    if not isinstance(response, dict):
        raise BridgeError("Apple Passwords 返回格式无效。")
    data = response.get("data", {})
    if not isinstance(data, dict):
        raise BridgeError("Apple Passwords 返回格式无效。")
    status = data.get("STATUS", response.get("status"))
    if type(status) is not int:
        raise BridgeError("Apple Passwords 返回状态无效。")
    if status == 9:
        raise BridgeError("Apple 配对已失效，请重试。")
    if status == 3:
        raise CredentialLookupError("未找到匹配的密码或验证码。")
    if status != 0:
        raise BridgeError("Apple Passwords 读取失败；未输出原始响应。")
    entries = data.get("Entries")
    if entries is None:
        entries = [v for k, v in data.items() if k.startswith("Entry_")]
    if not isinstance(entries, list):
        raise BridgeError("Apple Passwords 返回了不支持的条目格式。")
    candidates = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("USR" if mode == "password" else "username", "")
        value = entry.get("PWD" if mode == "password" else "code")
        if username and name != username:
            continue
        if isinstance(value, str) and value and value != "Not Included":
            candidates.append(value)
    if not candidates:
        raise CredentialLookupError("未找到匹配的密码或验证码。")
    if len(candidates) != 1:
        raise CredentialLookupError("匹配到多个账号，尚未复制。请在网站域名后补充用户名。")
    return candidates[0]


def read_secret(mode, domain, username):
    with PasswordSession(domain, username, mode) as session:
        session.pair()
        return session.read(mode)
