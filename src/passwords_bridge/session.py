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
        if mode not in ("password", "password_list", "otp", "otp_list"):
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
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                state = self.evaluate(
                    "typeof globalThis.__passwordsBridge === 'object' ? "
                    "globalThis.__passwordsBridge.state() : null")
                if isinstance(state, dict) and state.get("nativeReady"):
                    self.set_scope(self.domain, self.username, self.mode)
                    return self
                time.sleep(0.1)
            raise BridgeError("Apple 密码 helper 初始化超时。")
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def evaluate(self, expression):
        return self.browser.evaluate(expression, self.worker)

    def state(self):
        # A wedged keepalive must not hold up the broker's control endpoint for 30s.
        return self.browser.evaluate("globalThis.__passwordsBridge.state()", self.worker, timeout=3)

    def set_scope(self, domain, username, mode):
        if mode not in ('password', 'password_list', 'otp', 'otp_list'):
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
            snapshot = self.state()
            state = snapshot.get("state")
            if state == desired and snapshot.get("nativeReady"):
                return
            if desired == "SessionKeySet" and state == "NotInSession":
                raise BridgeError("Apple 配对失败或已取消，请重新运行复制命令。")
            time.sleep(0.2)
        raise PairingTimeout("Apple 配对握手等待超时。")

    def wait_native_ready(self, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.state()
            if state.get("nativeReady"):
                return state
            time.sleep(0.1)
        raise PairingTimeout("Apple 密码 helper 连接等待超时。")

    def pair(self):
        self.wait_native_ready()
        if self.request({"cmd": 2}).get("status") != 0:
            raise BridgeError("无法请求 Apple 配对。")
        self.wait_state("MSG1Set", timeout=12)
        pin = self.prompt_pin()
        try:
            state = self.wait_native_ready(timeout=12)
            if state.get("state") != "MSG1Set":
                raise BridgeError("Apple 配对连接已重置，请重新运行复制命令。")
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

    def query(self, mode):
        if mode == "password":
            message = {"cmd": 5, "qid": "CmdGetPassword4LoginName", "tabId": 0, "frameId": 0,
                       "url": self.domain,
                       "body": {"ACT": 2, "URL": self.domain, "USR": self.username}}
        elif mode == "password_list":
            message = {"cmd": 4, "qid": "CmdGetLoginNames4URL", "tabId": 0, "frameId": 0,
                       "url": self.domain,
                       "body": {"ACT": 5, "URL": self.domain}}
        elif mode in ("otp", "otp_list"):
            metadata_only = mode == 'otp_list'
            message = {"cmd": 16 if metadata_only else 17, "qid": "CmdDidFillOneTimeCode", "tabId": 0, "frameId": 0,
                       "body": {"ACT": 5 if metadata_only else 2, "TYPE": "oneTimeCodes", "frameURLs": ["https://" + self.domain]}}
        else:
            raise BridgeError("不支持的读取类型。")
        return self.request(message)

    def entries(self, mode):
        return decode_entries(self.query(mode))

    def read(self, mode):
        return select_secret(self.query(mode), mode, self.username)

    def __exit__(self, *exc):
        try:
            if self.browser:
                self.browser.close()
        finally:
            if self.temporary:
                self.temporary.cleanup()


class CredentialLookupError(BridgeError):
    """A valid paired session may be retained after an empty/ambiguous lookup."""


class PairingTimeout(BridgeError):
    """A native SRP phase did not advance within its bounded wait."""


def decode_entries(response):
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
        return []
    if status != 0:
        raise BridgeError("Apple Passwords 读取失败；未输出原始响应。")
    entries = data.get("Entries")
    if entries is None:
        entries = [v for k, v in data.items() if k.startswith("Entry_")]
    if not isinstance(entries, list):
        raise BridgeError("Apple Passwords 返回了不支持的条目格式。")
    return entries


def select_secret(response, mode, username):
    entries = decode_entries(response)
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
