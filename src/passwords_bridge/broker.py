"""Bounded, copy-only session broker. Passwords never cross the client socket."""

from concurrent.futures import ThreadPoolExecutor
import contextlib
import ctypes
import fcntl
import hmac
import json
import os
from pathlib import Path
import secrets
import select
import socket
import stat
import tempfile
import threading
import time

from .browser import BridgeError
from .clipboard import copy_secret
from .extension import data_dir
from .screen import LockMonitor
from .session import CredentialLookupError, PasswordSession
from .aliases import AliasStore, alias_name


PROTOCOL = 1
DEFAULT_IDLE = 300
MAX_LIFETIME = 1800
MAX_PACKET = 16384


class SessionExpired(BridgeError):
    pass


def validate_copy(request):
    if set(request) != {'op', 'mode', 'domain', 'username', 'clear_after', 'idle_seconds'}:
        raise BridgeError('复制请求格式无效。')
    if request['op'] != 'copy' or request['mode'] not in ('password', 'otp'):
        raise BridgeError('不支持的复制类型。')
    domain, username = request['domain'], request['username']
    if not isinstance(domain, str) or not domain.isascii() or not 1 <= len(domain) <= 253:
        raise BridgeError('网站域名无效。')
    for label in domain.split('.'):
        if (not label or len(label) > 63 or not label[0].isalnum() or not label[-1].isalnum() or
                any(not (char.isalnum() or char == '-') for char in label)):
            raise BridgeError('网站域名无效。')
    if not isinstance(username, str) or len(username) > 1024 or any(ord(c) < 32 for c in username):
        raise BridgeError('用户名无效。')
    for key, low, high in (('clear_after', 0, 86400), ('idle_seconds', 30, 1800)):
        if type(request[key]) is not int or not low <= request[key] <= high:
            raise BridgeError('会话或剪贴板时长超出允许范围。')
    return request


def validate_login(request):
    keys = {'op', 'domain', 'username', 'alias', 'save_alias', 'clear_after', 'idle_seconds', 'display', 'display_seconds'}
    if set(request) != keys or request['op'] != 'login':
        raise BridgeError('账号查询请求格式无效。')
    if not isinstance(request['alias'], str) or not isinstance(request['save_alias'], str):
        raise BridgeError('别名参数无效。')
    if request['alias']:
        alias_name(request['alias'])
        if request['domain'] or request['username'] or request['save_alias']:
            raise BridgeError('别名查询不能同时指定其他账号。')
    else:
        validate_copy({key: request[key] for key in ('domain', 'username', 'clear_after', 'idle_seconds')} |
                      {'op': 'copy', 'mode': 'password'})
    if request['save_alias']:
        alias_name(request['save_alias'])
    if (type(request['display']) is not bool or type(request['display_seconds']) is not int or
            not 10 <= request['display_seconds'] <= 180):
        raise BridgeError('显示设置无效。')
    # Alias requests still need the same numeric limits as ordinary copies.
    for key, low, high in (('clear_after', 0, 86400), ('idle_seconds', 30, 1800)):
        if type(request[key]) is not int or not low <= request[key] <= high:
            raise BridgeError('会话或剪贴板时长无效。')
    return request


class SessionManager:
    def __init__(self, monitor, session_factory=PasswordSession, copier=copy_secret,
                 clock=time.monotonic, wall_clock=time.time, idle=DEFAULT_IDLE, maximum=MAX_LIFETIME,
                 log=lambda event: None, alias_store=None, ui_factory=None):
        self.monitor, self.factory, self.copier = monitor, session_factory, copier
        self.clock, self.wall = clock, wall_clock
        self.idle, self.maximum = idle, maximum
        self.started = self.last_activity = self.last_tick = self.last_poke = clock()
        self.last_wall = wall_clock()
        self.session = None
        self.busy = False
        self.expired = False
        self.cancelled = threading.Event()
        self.log = log
        self.alias_store = alias_store
        self.ui_factory = ui_factory

    def ensure_session(self, domain, username, mode):
        if self.session is None:
            self.session = self.factory(domain, username, mode, guard=self.guard)
            self.session.__enter__()
        self.session.set_scope(domain, username, mode)
        if not self.session.state().get('paired'):
            self.session.pair()
            self.log('paired')

    def guard(self):
        now, wall = self.clock(), self.wall()
        reason = None
        if self.cancelled.is_set():
            reason = 'manual_lock'
        elif self.monitor.blocked():
            reason = 'screen_locked'
        elif now - self.started >= self.maximum:
            reason = 'maximum_lifetime'
        elif now - self.last_tick > 15 or wall - self.last_wall > 15 or wall < self.last_wall - 5:
            reason = 'sleep_or_clock_gap'
        elif not self.busy and now - self.last_activity >= self.idle:
            reason = 'idle_expired'
        self.last_tick, self.last_wall = now, wall
        if reason:
            if not self.expired:
                self.log(reason)
            self.expired = True
            raise SessionExpired('会话已锁定或超时，请解锁后重新运行复制命令。')

    def close_session(self):
        owned, self.session = self.session, None
        if owned:
            owned.__exit__(None, None, None)

    def tick(self):
        try:
            self.guard()
        except SessionExpired:
            self.close_session()
            raise
        if self.session and self.clock() - self.last_poke >= 2:
            self.last_poke = self.clock()
            try:
                if not self.session.state().get('paired'):
                    self.close_session()
                    self.log('apple_session_expired')
            except SessionExpired:
                raise
            except Exception:
                self.close_session()
                self.log('browser_disconnected')

    def copy(self, request):
        request = validate_copy(request)
        self.guard()
        self.busy = True
        self.idle = request['idle_seconds']
        value = None
        try:
            self.ensure_session(request['domain'], request['username'], request['mode'])
            self.guard()
            value = self.session.read(request['mode'])
            self.guard()  # a lock arriving during the read must prevent clipboard delivery
            self.copier(value, request['clear_after'])
            self.guard()
            self.log('copied')
            return {'ok': True, 'message': ('密码' if request['mode'] == 'password' else '验证码') + '已复制。'}
        except CredentialLookupError:
            raise  # keep pairing when the user only needs to refine the username
        except BaseException:
            self.close_session()
            raise
        finally:
            value = None
            try:
                if self.session:
                    self.session.clear_scope()
            except Exception:
                self.close_session()
            self.last_activity = self.clock()
            self.busy = False

    def login(self, request):
        from .login_flow import LoginFlow
        request = validate_login(request)
        self.guard()
        self.busy = True
        self.idle = request['idle_seconds']
        try:
            store = self.alias_store or AliasStore()
            flow = LoginFlow(self, store, request)
            if self.ui_factory:
                flow.ui = self.ui_factory(self, flow)
            return flow.run()
        except CredentialLookupError:
            raise
        except BaseException:
            self.close_session()
            raise
        finally:
            try:
                if self.session:
                    self.session.clear_scope()
            except Exception:
                self.close_session()
            self.last_activity = self.clock()
            self.busy = False


def peer_uid(connection):
    libc = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    libc.getpeereid.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
    uid, gid = ctypes.c_uint(), ctypes.c_uint()
    if libc.getpeereid(connection.fileno(), ctypes.byref(uid), ctypes.byref(gid)) != 0:
        raise BridgeError('无法验证本地连接身份。')
    return uid.value


def read_message(connection):
    connection.settimeout(0.25)
    data, deadline = b'', time.monotonic() + 2
    while b'\n' not in data and time.monotonic() < deadline:
        try:
            part = connection.recv(min(4096, MAX_PACKET + 1 - len(data)))
        except socket.timeout:
            continue
        if not part:
            break
        data += part
        if len(data) > MAX_PACKET:
            raise BridgeError('本地请求过大。')
    if b'\n' not in data:
        raise BridgeError('本地请求不完整。')
    message = json.loads(data.split(b'\n', 1)[0])
    if not isinstance(message, dict):
        raise BridgeError('本地请求格式无效。')
    return message


def authorize(message, token, uid):
    if uid != os.geteuid() or type(message.get('v')) is not int or message['v'] != PROTOCOL:
        raise BridgeError('本地请求未通过身份检查。')
    presented = message.get('token')
    if (not isinstance(presented, str) or not presented.isascii() or len(presented) != 64 or
            not hmac.compare_digest(presented, token)):
        raise BridgeError('本地请求未通过身份检查。')
    return {k: v for k, v in message.items() if k not in ('token', 'v')}


def send_result(connection, result):
    try:
        connection.settimeout(1)
        connection.sendall(json.dumps(result, ensure_ascii=False).encode() + b'\n')
    except OSError:
        pass
    finally:
        connection.close()


def metadata_write(root, token):
    fd, temporary = tempfile.mkstemp(prefix='session-meta-', dir=str(root))
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump({'v': PROTOCOL, 'pid': os.getpid(), 'token': token, 'interactive': True}, stream)
        os.replace(temporary, root / 'session.json')
    finally:
        Path(temporary).unlink(missing_ok=True)


def event_logger(root):
    path = root / 'session.log'
    if path.exists() and path.stat().st_size > 65536:
        os.replace(path, root / 'session.log.1')
    def log(event):
        # Callers provide only fixed event names, never requests, secrets, or exceptions.
        try:
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'a') as stream:
                stream.write(str(int(time.time())) + ' ' + event + '\n')
        except OSError:
            pass
    return log


def perform(manager, request):
    try:
        return manager.login(request) if request.get('op') == 'login' else manager.copy(request)
    except BridgeError as error:
        return {'ok': False, 'message': str(error)}
    except Exception:
        manager.log('request_failed')
        return {'ok': False, 'message': '复制失败，原始响应未输出。请重试。'}


def serve(root=None, monitor_factory=LockMonitor, session_factory=PasswordSession,
          copier=copy_secret, ready=None, ui_factory=None):
    root = Path(root) if root is not None else data_dir()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    lock = os.open(root / 'session.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(lock)
        return 0
    listener = monitor = manager = executor = future = pending = None
    socket_path = root / 'session.sock'
    owned_socket = False
    log = event_logger(root)
    try:
        monitor = monitor_factory()
        manager = SessionManager(monitor, session_factory, copier, log=log,
                                 alias_store=AliasStore(root), ui_factory=ui_factory)
        manager.guard()
        if socket_path.exists():
            if not stat.S_ISSOCK(socket_path.lstat().st_mode):
                raise BridgeError('会话地址被其他文件占用。')
            socket_path.unlink()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(socket_path))
        socket_path.chmod(0o600)
        owned_socket = True
        listener.listen(4)
        token = secrets.token_hex(32)
        metadata_write(root, token)
        executor = ThreadPoolExecutor(max_workers=1)
        log('started')
        if ready:
            ready.set()
        while True:
            if future is not None and future.done():
                send_result(pending, future.result())
                pending = future = None
            if future is None:
                manager.tick()
            if manager.cancelled.is_set() or manager.expired:
                break
            if not select.select([listener], [], [], 0.1)[0]:
                continue
            connection, _ = listener.accept()
            try:
                request = authorize(read_message(connection), token, peer_uid(connection))
                if request == {'op': 'stop'}:
                    manager.cancelled.set()
                    send_result(connection, {'ok': True, 'message': '正在关闭会话。'})
                elif request == {'op': 'status'}:
                    send_result(connection, {'ok': True, 'active': manager.session is not None,
                                             'busy': future is not None,
                                             'idle_remaining': max(0, int(manager.idle - (manager.clock() - manager.last_activity)))})
                elif request.get('op') in ('copy', 'login'):
                    (validate_login if request['op'] == 'login' else validate_copy)(request)
                    if future is not None:
                        raise BridgeError('已有复制操作进行中，请完成或取消配对后重试。')
                    pending = connection
                    future = executor.submit(perform, manager, request)
                else:
                    raise BridgeError('不支持的会话操作。')
            except BridgeError as error:
                send_result(connection, {'ok': False, 'message': str(error)})
            except Exception:
                send_result(connection, {'ok': False, 'message': '本地请求无效。'})
    except SessionExpired:
        pass
    except Exception:
        log('startup_or_service_failed')
        return 1
    finally:
        if manager:
            manager.cancelled.set()
        if listener:
            listener.close()
        if executor:
            executor.shutdown(wait=True)
        if pending:
            send_result(pending, {'ok': False, 'message': '会话已关闭。'})
        if manager:
            with contextlib.suppress(Exception):
                manager.close_session()
        if monitor:
            with contextlib.suppress(Exception):
                monitor.close()
        if owned_socket:
            socket_path.unlink(missing_ok=True)
            (root / 'session.json').unlink(missing_ok=True)
        log('stopped')
        os.close(lock)
    return 0
