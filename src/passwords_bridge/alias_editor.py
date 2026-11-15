"""Short-lived metadata drafts for typing alias names in Alfred's own field."""

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import secrets
import tempfile
import time

from .aliases import AliasStore, alias_name, validate_binding
from .browser import BridgeError
from .extension import data_dir


class AliasEditor:
    def __init__(self, root=None, clock=time.time):
        self.root = Path(root) if root is not None else data_dir()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = self.root / 'alias-draft.json'
        self.clock = clock

    @contextmanager
    def _locked(self):
        fd = os.open(self.root / 'alias-draft.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _read(self):
        try:
            if not self.path.exists(): return None
            if self.path.stat().st_size > 262144: raise ValueError
            draft = json.loads(self.path.read_text())
            if (not isinstance(draft, dict) or set(draft) != {'id', 'created', 'binding'} or
                    not isinstance(draft['id'], str) or len(draft['id']) != 32 or
                    type(draft['created']) not in (int, float)):
                raise ValueError
            validate_binding(draft['binding'])
            age = self.clock() - draft['created']
            if age < 0 or age > 300:
                self.path.unlink(missing_ok=True)
                return None
            return draft
        except (OSError, ValueError, TypeError):
            raise BridgeError('别名编辑状态无效，请重新选择账号。') from None

    def begin(self, binding):
        binding = validate_binding(binding)
        # Round-trip validated metadata so later mutations cannot change the target.
        draft = {'id': secrets.token_hex(16), 'created': self.clock(),
                 'binding': json.loads(json.dumps(binding))}
        with self._locked():
            fd, temporary = tempfile.mkstemp(prefix='alias-draft-', dir=str(self.root))
            try:
                with os.fdopen(fd, 'w') as stream:
                    json.dump(draft, stream, ensure_ascii=False)
                os.replace(temporary, self.path)
            finally:
                Path(temporary).unlink(missing_ok=True)
        return draft

    def current(self):
        with self._locked(): return self._read()

    def save(self, draft_id, name, replace=False):
        name = alias_name(name)
        with self._locked():
            draft = self._read()
            if draft is None or draft['id'] != draft_id:
                raise BridgeError('别名编辑目标已过期或改变，请重新选择账号。')
            AliasStore(self.root).put(name, draft['binding'], replace=replace)
            self.path.unlink(missing_ok=True)

    def discard(self):
        with self._locked(): self.path.unlink(missing_ok=True)
