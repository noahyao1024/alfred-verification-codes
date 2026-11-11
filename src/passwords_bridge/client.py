"""Local copy requests; no credential is accepted in a broker response."""

import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import time

from .browser import BridgeError
from .broker import PROTOCOL, validate_catalog, validate_copy, validate_login, validate_select, validate_warm
from .extension import data_dir


class Unavailable(Exception):
    pass


class SessionChanged(Exception):
    pass


def metadata(root):
    path = root / 'session.json'
    try:
        mode = path.lstat()
        if not stat.S_ISREG(mode.st_mode) or mode.st_uid != os.geteuid() or stat.S_IMODE(mode.st_mode) != 0o600:
            raise BridgeError('会话凭据文件权限异常。')
        info = json.loads(path.read_text())
    except FileNotFoundError:
        raise Unavailable from None
    except (ValueError, OSError):
        raise BridgeError('无法读取本机会话信息。') from None
    if info.get('v') != PROTOCOL or not isinstance(info.get('token'), str) or len(info['token']) != 64:
        raise BridgeError('会话版本或凭据无效。请运行 pwlock 后重试。')
    return info


def rpc(request, root=None, expected_token=None, return_token=False):
    root = Path(root) if root is not None else data_dir()
    info = metadata(root)
    if expected_token is not None and info['token'] != expected_token:
        raise SessionChanged
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connection.settimeout(1)
        try:
            connection.connect(str(root / 'session.sock'))
        except (FileNotFoundError, ConnectionRefusedError):
            raise Unavailable from None
        # Interactive account/popup steps can legitimately exceed a PIN-only wait.
        # The broker's 30-minute lifetime cap still bounds the entire interaction.
        connection.settimeout(1860 if request.get('op') in ('login', 'catalog', 'select') else 180)
        packet = dict(request, v=PROTOCOL, token=info['token'])
        connection.sendall(json.dumps(packet).encode() + b'\n')
        reply = b''
        while b'\n' not in reply:
            part = connection.recv(4096)
            if not part or len(reply) + len(part) > 262144:
                raise BridgeError('会话连接中断，结果未确认；请先检查剪贴板。')
            reply += part
        result = json.loads(reply.split(b'\n', 1)[0])
        if not isinstance(result, dict) or type(result.get('ok')) is not bool:
            raise BridgeError('会话响应格式无效。')
        return (result, info['token']) if return_token else result
    except (socket.timeout, ConnectionResetError, BrokenPipeError):
        # Never retry after transmitting a copy: it may already have happened.
        raise BridgeError('操作结果未确认，请先检查剪贴板后再重试。') from None
    finally:
        connection.close()


def request_copy(mode, domain, username, clear_after, idle_seconds):
    request = validate_copy({'op': 'copy', 'mode': mode, 'domain': domain, 'username': username,
                             'clear_after': clear_after, 'idle_seconds': idle_seconds})
    retire_incompatible_broker()
    return dispatch(request)


def request_warm(idle_seconds):
    request = validate_warm({'op': 'warm', 'idle_seconds': idle_seconds})
    retire_incompatible_broker()
    return dispatch(request)


def request_login(domain, username, alias, save_alias, clear_after, idle_seconds, display, display_seconds):
    request = validate_login({'op': 'login', 'domain': domain, 'username': username,
                              'alias': alias, 'save_alias': save_alias, 'clear_after': clear_after,
                              'idle_seconds': idle_seconds, 'display': display, 'display_seconds': display_seconds})
    retire_incompatible_broker()
    return dispatch(request)


def request_catalog(domain, mode, idle_seconds):
    request = validate_catalog({'op': 'catalog', 'mode': mode, 'domain': domain,
                                'idle_seconds': idle_seconds})
    retire_incompatible_broker()
    return dispatch(request)


def request_selected(domain, identity, clear_after, idle_seconds, display, display_seconds):
    request = validate_select({'op': 'select', 'domain': domain, 'identity': identity,
                               'clear_after': clear_after, 'idle_seconds': idle_seconds,
                               'display': display, 'display_seconds': display_seconds})
    retire_incompatible_broker()
    return dispatch(request)


def retire_incompatible_broker():
    """Replace a pre-catalog broker before sending a new protocol operation."""
    try:
        info = metadata(data_dir())
        if (not info.get('alfred_catalog') or not info.get('performance_v1') or
                not info.get('native_ready_v1') or not info.get('prewarm_v1')):
            result = lock_session()
            if not result['ok']:
                raise BridgeError(result['message'])
    except Unavailable:
        pass


def dispatch(request):
    entry = Path(__file__).resolve().parents[1] / 'apple-passwords.py'
    launched = False
    startup_deadline = warming_deadline = None
    warming_token = None
    while True:
        now = time.monotonic()
        if warming_deadline is not None and now >= warming_deadline:
            raise BridgeError('浏览器预热超时，请重试。')
        try:
            result, token = rpc(request, expected_token=warming_token, return_token=True)
        except SessionChanged:
            raise BridgeError('预热会话已被锁定或替换，请重新运行操作。') from None
        except Unavailable:
            if warming_token is not None:
                raise BridgeError('预热会话已关闭，请重新运行操作。') from None
            if not launched:
                subprocess.Popen([sys.executable, '-I', str(entry), '--serve'], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                launched = True
                startup_deadline = time.monotonic() + 10
            elif time.monotonic() >= startup_deadline:
                raise BridgeError('无法启动会话，请解锁屏幕后重试；也可运行 pwlock 清理旧会话。')
            time.sleep(0.1)
            continue
        if result.get('warming') is True:
            # The broker has not submitted this operation, so retrying it is safe.
            warming_token = token
            if warming_deadline is None:
                warming_deadline = time.monotonic() + 30
            time.sleep(0.1)
            continue
        return result


def lock_session():
    root = data_dir()
    try:
        result = rpc({'op': 'stop'}, root)
    except Unavailable:
        return {'ok': True, 'message': '没有活动的配对会话。'}
    if not result['ok']:
        return result
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if not (root / 'session.json').exists():
            return {'ok': True, 'message': '会话已锁定，下次复制需要重新配对。'}
        time.sleep(0.1)
    return {'ok': False, 'message': '会话仍在关闭，请稍后重试 pwlock。'}
