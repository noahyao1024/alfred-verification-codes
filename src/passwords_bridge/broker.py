"""Bounded session broker. Metadata may return; secrets never cross the client socket."""

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
from .performance import format_log_event, performance_event
from .screen import LockMonitor
from .session import CredentialLookupError, PasswordSession
from .aliases import AliasStore, alias_name


PROTOCOL = 1
DEFAULT_IDLE = 1800
MAX_LIFETIME = 1800
MAX_PACKET = 16384
CATALOG_TTL = MAX_LIFETIME


class SessionExpired(BridgeError):
    pass


class RequestCancelled(BridgeError):
    pass


class WarmSessionRequestCancelled(RequestCancelled):
    """A stale prefix query ended after producing a reusable browser session."""


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


def validate_catalog(request):
    if set(request) != {'op', 'mode', 'domain', 'idle_seconds'} or request['op'] != 'catalog':
        raise BridgeError('账号列表请求格式无效。')
    if request['mode'] not in ('password', 'otp'):
        raise BridgeError('账号列表类型无效。')
    validate_copy({'op': 'copy', 'mode': 'password', 'domain': request['domain'],
                   'username': '', 'clear_after': 0, 'idle_seconds': request['idle_seconds']})
    return request


def validate_select(request):
    keys = {'op', 'domain', 'identity', 'clear_after', 'idle_seconds', 'display', 'display_seconds'}
    if set(request) != keys or request['op'] != 'select':
        raise BridgeError('账号选择请求格式无效。')
    validate_copy({'op': 'copy', 'mode': 'password', 'domain': request['domain'],
                   'username': '', 'clear_after': request['clear_after'],
                   'idle_seconds': request['idle_seconds']})
    from .accounts import validate_identity
    request = dict(request)
    request['identity'] = validate_identity(request['identity'])
    if (type(request['display']) is not bool or type(request['display_seconds']) is not int or
            not 10 <= request['display_seconds'] <= 180):
        raise BridgeError('显示设置无效。')
    return request


class SessionManager:
    def __init__(self, monitor, session_factory=PasswordSession, copier=copy_secret,
                 clock=time.monotonic, wall_clock=time.time, idle=DEFAULT_IDLE, maximum=MAX_LIFETIME,
                 log=lambda event: None, alias_store=None, ui_factory=None, perf_clock=time.perf_counter):
        self.monitor, self.factory, self.copier = monitor, session_factory, copier
        self.clock, self.wall = clock, wall_clock
        self.idle, self.maximum = idle, maximum
        self.started = self.last_activity = self.last_tick = self.last_poke = clock()
        self.last_wall = wall_clock()
        self.session = None
        self.busy = False
        self.expired = False
        self.cancelled = threading.Event()
        self.request_cancelled = threading.Event()
        self.starting_session = False
        self.log = log
        self.perf_clock = perf_clock
        self.alias_store = alias_store
        self.ui_factory = ui_factory
        self.password_cache = {}
        self.copy_cache = {}
        self.catalog_cache = {}

    def clear_cache(self):
        self.password_cache.clear()
        self.copy_cache.clear()
        self.catalog_cache.clear()

    def record_performance(self, phase, started):
        self.log(performance_event(phase, started, self.perf_clock))

    def get_cached_password(self, identity):
        from .accounts import Account, identity_key
        key = identity_key(identity)
        value = self.password_cache.get(key)
        if not value:
            return None
        return Account(dict(identity), value)

    def put_cached_password(self, account):
        from .accounts import identity_key
        if account.kind == 'password' and account.value and not account.duplicate:
            self.password_cache[identity_key(account.identity)] = account.value

    def drop_cached_password(self, identity):
        from .accounts import identity_key
        self.password_cache.pop(identity_key(identity), None)

    def ensure_session(self, domain, username, mode):
        if self.session is None:
            self.clear_cache()
            started = self.perf_clock()
            candidate = self.factory(domain, username, mode, guard=self.guard)
            self.starting_session = True
            try:
                candidate.__enter__()
                self.session = candidate
                if self.request_cancelled.is_set():
                    # Alfred starts a query as soon as a short prefix is typed.
                    # Finish the one expensive browser startup, but do not pair or
                    # query that stale prefix. The newest rerun can reuse the browser.
                    candidate.clear_scope()
                    self.last_poke = self.clock()  # give Alfred's 0.5s rerun a warm-session grace period
                    raise WarmSessionRequestCancelled('Alfred 搜索内容已更新。')
            finally:
                self.starting_session = False
                self.record_performance('browser_startup', started)
        self.session.set_scope(domain, username, mode)
        if not self.session.state().get('paired'):
            self.clear_cache()
            self.log('pair_requested')
            started = self.perf_clock()
            try:
                self.session.pair()
            finally:
                self.record_performance('pairing', started)
            self.log('paired')

    def guard(self):
        now, wall = self.clock(), self.wall()
        reason = None
        if self.request_cancelled.is_set() and not self.starting_session:
            raise RequestCancelled('Alfred 搜索内容已更新。')
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
        self.clear_cache()
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
            cache_key = (request['domain'], request['username'])
            if request['mode'] == 'password' and self.session is not None and cache_key in self.copy_cache:
                self.guard()
                if self.session.state().get('paired'):
                    value = self.copy_cache[cache_key]
                    self.guard()
                    self.copier(value, request['clear_after'])
                    self.guard()
                    self.log('password_cache_hit')
                    self.log('copied')
                    return {'ok': True, 'message': '密码已复制。'}
                self.clear_cache()
            self.ensure_session(request['domain'], request['username'], request['mode'])
            self.guard()
            value = self.session.read(request['mode'])
            self.guard()  # a lock arriving during the read must prevent clipboard delivery
            if request['mode'] == 'password':
                self.copy_cache[cache_key] = value
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

    def _metadata_accounts(self, domain, kind):
        from .accounts import account_list
        mode = kind + '_list'
        self.ensure_session(domain, '', mode)
        started = self.perf_clock()
        try:
            self.guard()
            self.log(kind + '_metadata_requested')
            return account_list(self.session.entries(mode), kind, domain, metadata_only=True)
        finally:
            self.record_performance(kind + '_metadata', started)
            if self.session:
                self.session.clear_scope()

    @staticmethod
    def _public_accounts(accounts):
        return [{'identity': dict(account.identity), 'duplicate': account.duplicate} for account in accounts]

    def catalog(self, request):
        request = validate_catalog(request)
        self.guard()
        self.busy = True
        self.idle = request['idle_seconds']
        started = self.perf_clock()
        warm_cancel = False
        try:
            key = (request['domain'], request['mode'])
            cached = self.catalog_cache.get(key)
            if cached and self.clock() - cached[0] <= CATALOG_TTL:
                self.log('catalog_cache_hit')
                return {'ok': True, 'mode': cached[1],
                        'accounts': self._public_accounts(cached[2]),
                        'fallback': request['mode'] == 'password' and cached[1] == 'otp'}
            mode = request['mode']
            accounts = self._metadata_accounts(request['domain'], mode)
            # The common password path returns as soon as password metadata is
            # ready. Only an empty password catalogue pays for an OTP query.
            if mode == 'password' and not accounts:
                mode = 'otp'
                accounts = self._metadata_accounts(request['domain'], mode)
            cached = (self.clock(), mode, accounts)
            self.catalog_cache[key] = cached
            self.catalog_cache[(request['domain'], mode)] = cached
            return {'ok': True, 'mode': mode, 'accounts': self._public_accounts(accounts),
                    'fallback': request['mode'] == 'password' and mode == 'otp'}
        except WarmSessionRequestCancelled:
            warm_cancel = True
            raise
        except BaseException:
            self.close_session()
            raise
        finally:
            self.record_performance('catalog_total', started)
            try:
                if self.session and not warm_cancel:
                    self.session.clear_scope()
            except Exception:
                self.close_session()
            self.last_activity = self.clock()
            self.busy = False

    def select(self, request):
        from .login_flow import LoginFlow
        request = validate_select(request)
        self.guard()
        self.busy = True
        self.idle = request['idle_seconds']
        try:
            flow_request = {
                'op': 'login', 'domain': request['domain'],
                'username': request['identity']['username'], 'alias': '', 'save_alias': '',
                'clear_after': request['clear_after'], 'idle_seconds': request['idle_seconds'],
                'display': request['display'], 'display_seconds': request['display_seconds'],
            }
            store = self.alias_store or AliasStore()
            flow = LoginFlow(self, store, flow_request)
            if self.ui_factory:
                flow.ui = self.ui_factory(self, flow)
            return flow.run_selected(request['identity'])
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


def peer_disconnected(connection):
    try:
        if not select.select([connection], [], [], 0)[0]:
            return False
        return connection.recv(1, socket.MSG_PEEK) == b''
    except OSError:
        return True


def metadata_write(root, token):
    fd, temporary = tempfile.mkstemp(prefix='session-meta-', dir=str(root))
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump({'v': PROTOCOL, 'pid': os.getpid(), 'token': token,
                       'interactive': True, 'lazy_otp': True, 'lazy_passwords': True,
                       'credential_cache': True, 'alfred_catalog': True,
                       'performance_v1': True, 'native_ready_v1': True}, stream)
        os.replace(temporary, root / 'session.json')
    finally:
        Path(temporary).unlink(missing_ok=True)


def event_logger(root):
    path = root / 'session.log'
    if path.exists() and path.stat().st_size > 65536:
        os.replace(path, root / 'session.log.1')
    def log(event):
        # The formatter accepts only fixed event names or allowlisted phase + integer-ms records.
        payload = format_log_event(event)
        if payload is None:
            return
        try:
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'a') as stream:
                stream.write(str(int(time.time())) + ' ' + payload + '\n')
        except OSError:
            pass
    return log


def perform(manager, request):
    started = manager.perf_clock()
    try:
        operations = {'copy': manager.copy, 'login': manager.login,
                      'catalog': manager.catalog, 'select': manager.select}
        operation = operations.get(request.get('op'))
        if operation is None:
            raise BridgeError('不支持的会话操作。')
        return operation(request)
    except BridgeError as error:
        return {'ok': False, 'message': str(error)}
    except Exception:
        manager.log('request_failed')
        return {'ok': False, 'message': '复制失败，原始响应未输出。请重试。'}
    finally:
        manager.record_performance('request_total', started)


def serve(root=None, monitor_factory=LockMonitor, session_factory=PasswordSession,
          copier=copy_secret, ready=None, ui_factory=None):
    serve_started = time.perf_counter()
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
        log(performance_event('broker_startup', serve_started))
        if ready:
            ready.set()
        while True:
            if future is not None and future.done():
                send_result(pending, future.result())
                pending = future = None
                manager.request_cancelled.clear()
            elif (future is not None and pending is not None and
                  not manager.request_cancelled.is_set() and peer_disconnected(pending)):
                manager.request_cancelled.set()
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
                elif request.get('op') in ('copy', 'login', 'catalog', 'select'):
                    validators = {'copy': validate_copy, 'login': validate_login,
                                  'catalog': validate_catalog, 'select': validate_select}
                    request = validators[request['op']](request)
                    if future is not None:
                        raise BridgeError('已有复制操作进行中，请完成或取消配对后重试。')
                    manager.request_cancelled.clear()
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
