"""Transient native dialogs, cancellable by the session lock guard."""

import json
import os
from pathlib import Path
import select
import subprocess
import time

from .browser import BridgeError


class LocalUI:
    def __init__(self, guard=lambda: None, keepalive=lambda: None, timeout=90):
        self.guard, self.keepalive, self.timeout = guard, keepalive, timeout

    def run(self, payload, timeout=None):
        data = json.dumps(payload, ensure_ascii=False).encode()
        if len(data) > 2 * 1024 * 1024:
            raise BridgeError('显示内容过多，请缩小账号查询范围。')
        proc = subprocess.Popen(['/usr/bin/osascript', '-l', 'JavaScript',
                                 str(Path(__file__).with_suffix('.js'))], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + (timeout or self.timeout)
        last_poke = 0.0
        output = b''
        try:
            os.set_blocking(proc.stdin.fileno(), False)
            while data:
                self.guard()
                if proc.poll() is not None:
                    raise BridgeError('无法打开本机账号窗口。')
                if time.monotonic() >= deadline:
                    return {'action': 'cancel', 'timed_out': True}
                if select.select([], [proc.stdin], [], .1)[1]:
                    written = os.write(proc.stdin.fileno(), data[:16384])
                    data = data[written:]
            proc.stdin.close()
            while proc.poll() is None:
                self.guard()
                if select.select([proc.stdout], [], [], 0)[0]:
                    output += os.read(proc.stdout.fileno(), 4096)
                    if len(output) > 8192:
                        raise BridgeError('窗口返回内容过长。')
                now = time.monotonic()
                if now >= deadline:
                    return {'action': 'cancel', 'timed_out': True}
                if now - last_poke >= 1:
                    self.keepalive()
                    last_poke = now
                time.sleep(.1)
            if proc.returncode:
                raise BridgeError('本机账号窗口已关闭或无法显示。')
            output += proc.stdout.read(8193)
            if len(output) > 8192:
                raise BridgeError('窗口返回内容过长。')
            result = json.loads(output)
            if not isinstance(result, dict) or set(result) - {'action', 'index', 'text'}:
                raise BridgeError('窗口返回格式无效。')
            return result
        except (OSError, ValueError):
            raise BridgeError('本机窗口交互失败；原始内容未输出。') from None
        finally:
            if proc.poll() is None:
                proc.terminate()
                try: proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill(); proc.wait(timeout=3)
            if not proc.stdin.closed: proc.stdin.close()
            proc.stdout.close()

    def choose(self, accounts, title='选择账号', message='请选择要使用的账号。'):
        result = self.run({'kind': 'picker', 'title': title, 'message': message,
                           'items': [item.row(index) for index, item in enumerate(accounts)],
                           'buttons': [{'id': 'select', 'label': '选择'}, {'id': 'cancel', 'label': '取消'}]})
        if result.get('action') != 'select':
            return None
        index = result.get('index')
        if type(index) is not int or not 0 <= index < len(accounts):
            raise BridgeError('未选中有效账号。')
        chosen = accounts[index]
        if chosen.duplicate:
            raise BridgeError('这些记录的网站、用户名和标题完全相同，请先在「密码」App 中区分后再选择。')
        return chosen

    def text(self, title, message, initial=''):
        result = self.run({'kind': 'text', 'title': title, 'message': message, 'initial': initial,
                           'buttons': [{'id': 'accept', 'label': '确定'}, {'id': 'cancel', 'label': '取消'}]})
        return result.get('text') if result.get('action') == 'accept' else None

    def confirm(self, title, message):
        return self.run({'kind': 'message', 'title': title, 'message': message,
                         'buttons': [{'id': 'accept', 'label': '更新别名'}, {'id': 'cancel', 'label': '取消'}]}).get('action') == 'accept'

    def message(self, title, message):
        self.run({'kind': 'message', 'title': title, 'message': message})

    def copied(self, account, value, has_otp, show, timeout, can_password=False):
        if account.kind == 'otp' or not show:
            return 'done'
        buttons = [{'id': 'done', 'label': '完成'}]
        if account.kind == 'password' and has_otp:
            buttons.append({'id': 'otp', 'label': '复制验证码'})
        elif account.kind == 'otp':
            buttons.append({'id': 'refresh', 'label': '刷新并复制验证码'})
        if can_password:
            buttons.append({'id': 'password', 'label': '返回并复制密码'})
        elif account.kind == 'password':
            buttons.append({'id': 'refresh_password', 'label': '重新读取密码'})
        buttons.append({'id': 'alias', 'label': '设置别名…'})
        result = self.run({'kind': 'secret', 'title': '密码已复制' if account.kind == 'password' else '验证码已复制',
                           'message': account.label + ' · ' + account.username + '\n' + ' · '.join(account.sites),
                           'value': value if show else '', 'show': show, 'buttons': buttons}, timeout=timeout)
        return result.get('action', 'done')
