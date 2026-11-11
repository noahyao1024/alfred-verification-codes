"""Private, atomic alias storage. Only account metadata is accepted."""

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import tempfile

from .browser import BridgeError
from .extension import data_dir
from .accounts import host


class AliasExists(BridgeError):
    pass


def alias_name(name):
    if not isinstance(name, str):
        raise BridgeError('别名必须是文字。')
    name = name.strip().lstrip('@')
    if (not 1 <= len(name) <= 64 or any(not (c.isalnum() or c in '-_') for c in name) or
            name.casefold() in {'alias', 'setup', 'lock', 'help'}):
        raise BridgeError('别名请使用 1–64 个字母、数字、中文、横线或下划线，避开 alias/setup/lock/help。')
    return name


def validate_binding(binding):
    if not isinstance(binding, dict) or set(binding) != {'domain', 'identity'}:
        raise BridgeError('别名绑定格式无效。')
    identity = binding['identity']
    if (not isinstance(identity, dict) or set(identity) != {'kind', 'username', 'title', 'sites'} or
            identity['kind'] not in ('password', 'otp') or
            not isinstance(binding['domain'], str) or not 1 <= len(binding['domain']) <= 253 or
            host(binding['domain']) != binding['domain'] or
            any(not isinstance(identity[key], str) or len(identity[key]) > 1024 for key in ('username', 'title')) or
            not isinstance(identity['sites'], list) or len(identity['sites']) > 100 or
            any(not isinstance(site, str) or len(site) > 2048 for site in identity['sites'])):
        raise BridgeError('别名账号标识无效。')
    return binding


class AliasStore:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else data_dir()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = self.root / 'aliases.json'

    @contextmanager
    def _locked(self):
        fd = os.open(self.root / 'aliases.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _load(self):
        if not self.path.exists():
            return {}
        try:
            if self.path.stat().st_size > 2 * 1024 * 1024:
                raise ValueError
            document = json.loads(self.path.read_text())
            if not isinstance(document, dict) or document.get('version') != 1 or not isinstance(document.get('aliases'), dict):
                raise ValueError
            aliases = document['aliases']
            for key, record in aliases.items():
                if set(record) != {'name', 'domain', 'identity'} or alias_name(record['name']).casefold() != key:
                    raise ValueError
                validate_binding({k: record[k] for k in ('domain', 'identity')})
            return aliases
        except (ValueError, TypeError, KeyError, OSError):
            raise BridgeError('别名文件损坏，未覆盖原文件。请检查 aliases.json。') from None

    def _save(self, aliases):
        if len(aliases) > 1000:
            raise BridgeError('别名数量超过 1000 个。')
        content = json.dumps({'version': 1, 'aliases': aliases}, ensure_ascii=False, indent=2)
        if len(content.encode()) > 2 * 1024 * 1024:
            raise BridgeError('别名文件超过大小限制。')
        fd, temporary = tempfile.mkstemp(prefix='aliases-', dir=str(self.root))
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write(content)
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def all(self):
        with self._locked():
            return sorted(self._load().values(), key=lambda record: record['name'].casefold())

    def get(self, name):
        key = alias_name(name).casefold()
        with self._locked():
            value = self._load().get(key)
        if value is None:
            raise BridgeError('别名不存在，请输入 pass alias 查看。')
        return value

    def put(self, name, binding, replace=False):
        name = alias_name(name)
        binding = validate_binding(binding)
        record = dict(binding, name=name)
        with self._locked():
            aliases = self._load()
            existing = aliases.get(name.casefold())
            if existing and existing != record and not replace:
                raise AliasExists('这个别名已经绑定其他账号。')
            aliases[name.casefold()] = record
            self._save(aliases)

    def remove(self, name):
        key = alias_name(name).casefold()
        with self._locked():
            aliases = self._load()
            found = aliases.pop(key, None) is not None
            self._save(aliases)
            return found

    def rename(self, old, new):
        old, new = alias_name(old), alias_name(new)
        with self._locked():
            aliases = self._load()
            if old.casefold() not in aliases:
                raise BridgeError('待重命名的别名不存在。')
            if new.casefold() in aliases and new.casefold() != old.casefold():
                raise AliasExists('目标别名已经存在。')
            record = aliases.pop(old.casefold())
            aliases[new.casefold()] = dict(record, name=new)
            self._save(aliases)
