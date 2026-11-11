"""Account picker -> password -> optional fresh OTP, entirely inside the broker."""

from .accounts import account_list, host, otp_matches, resolve_identity, resolve_otp_metadata
from .aliases import AliasExists, alias_name
from .browser import BridgeError
from .local_ui import LocalUI
from .session import CredentialLookupError


class LoginFlow:
    def __init__(self, manager, store, request, ui=None):
        self.manager, self.store, self.request = manager, store, request
        self.ui = ui or LocalUI(manager.guard, self.keepalive)
        self.domain, self.username = request['domain'], request['username']
        self.binding = None
        if request['alias']:
            self.binding = store.get(request['alias'])
            self.domain = self.binding['domain']
            self.username = self.binding['identity']['username']
        self.did_password = self.did_otp = False

    def keepalive(self):
        if self.manager.session and not self.manager.session.state().get('paired'):
            raise BridgeError('Apple 配对已失效，请重新查询账号。')

    def fetch(self, kind, username=None):
        user = self.username if username is None else username
        self.manager.ensure_session(self.domain, user, kind)
        try:
            self.manager.guard()
            self.manager.log({'password': 'password_requested', 'otp': 'otp_requested', 'otp_list': 'otp_metadata_requested'}[kind])
            return account_list(self.manager.session.entries(kind), 'otp' if kind == 'otp_list' else kind,
                                self.domain, user, metadata_only=kind == 'otp_list')
        finally:
            self.manager.session.clear_scope()

    def save_alias(self, account, name=''):
        if account.duplicate:
            raise CredentialLookupError('同名重复记录不能绑定稳定别名，请先在「密码」App 中区分。')
        if not name:
            name = self.ui.text('设置别名', '输入简短别名，例如 gh-work。以后输入 pass gh-work 即可使用。')
        if name is None:
            return False
        name = alias_name(name)
        binding = {'domain': self.domain, 'identity': account.identity}
        try:
            self.store.put(name, binding)
        except AliasExists:
            if not self.ui.confirm('更新已有别名？', name + ' 将绑定：\n' + account.label + '\n' + account.username):
                return False
            self.store.put(name, binding, replace=True)
        self.manager.log('alias_saved')
        return True

    def copy(self, account):
        if not account.value:
            raise CredentialLookupError('账号值尚未读取，未修改剪贴板。')
        self.manager.guard()
        self.manager.copier(account.value, self.request['clear_after'])
        self.manager.guard()
        self.did_password |= account.kind == 'password'
        self.did_otp |= account.kind == 'otp'
        self.manager.log('copied_password' if account.kind == 'password' else 'copied_otp')

    def fresh_code(self, primary, identity=None):
        if identity is not None:
            return resolve_identity(self.fetch('otp', primary.username), identity)
        values_loaded = False
        try:
            codes = self.fetch('otp_list', primary.username)
        except BridgeError:
            self.manager.guard()
            self.manager.log('otp_metadata_unavailable')
            # Compatibility fallback is allowed only after the user explicitly
            # requests an OTP, never while they are merely choosing a password.
            codes, values_loaded = self.fetch('otp', primary.username), True
        matches = otp_matches(primary, codes) if primary.kind == 'password' else codes
        if not matches:
            raise CredentialLookupError('这个账号没有可用验证码。')
        exact_site = len(matches) == 1 and any(host(site) in {host(value) for value in primary.sites} for site in matches[0].sites)
        # The OTP button already confirms a single exact match. Broader domains
        # or multiple factors require their own explicit selection.
        selected = matches[0] if exact_site and not matches[0].duplicate else self.ui.choose(
            matches, '选择验证码', '核对网站和用户名；确认后复制当前验证码。')
        if selected is None:
            return None
        if values_loaded and exact_site and not selected.duplicate:
            return selected  # already fresh, and no picker delay occurred
        # Selection uses metadata only. Read the secret once, after confirmation.
        return resolve_otp_metadata(self.fetch('otp', primary.username), selected.identity)

    def run(self):
        # An OTP alias already identifies the requested factor. Do not read
        # passwords or a metadata catalogue before its one explicit secret read.
        if self.binding and self.binding['identity']['kind'] == 'otp':
            primary = resolve_otp_metadata(self.fetch('otp'), self.binding['identity'])
            return self.present(primary, False)

        passwords = self.fetch('password')
        otp_error = False
        try:
            codes = self.fetch('otp_list')
        except BridgeError:
            self.manager.guard()  # never downgrade a screen lock/cancellation to "no OTP"
            self.manager.log('otp_metadata_unavailable')
            if not passwords:
                codes = self.fetch('otp')  # no password exists; OTP is the requested fallback
            else:
                codes, otp_error = [], True
        # Accounts with a password enter through that password; OTP-only accounts
        # remain selectable instead of disappearing behind another user's password.
        names_with_passwords = {account.username for account in passwords}
        accounts = passwords + [code for code in codes if code.username not in names_with_passwords]
        if not accounts:
            raise CredentialLookupError('这个网站没有匹配的密码或验证码。')
        if self.binding:
            primary = resolve_identity(passwords, self.binding['identity'])
        else:
            primary = self.ui.choose(accounts, '选择账号', self.domain + ' · 可搜索用户名、名称或网站；优先复制密码。')
        if primary is None:
            return {'ok': True, 'message': '已取消账号选择，未复制新内容。'}
        if primary.duplicate:
            raise CredentialLookupError('这些记录的可见标识完全相同，请先在「密码」App 中区分。')
        if self.request['save_alias']:
            saved = self.save_alias(primary, self.request['save_alias'])
            return {'ok': True, 'message': '别名已保存。' if saved else '已取消保存别名。'}
        if primary.kind == 'otp':
            primary = resolve_otp_metadata(self.fetch('otp', primary.username), primary.identity)
        has_otp = bool(otp_matches(primary, codes)) or otp_error
        return self.present(primary, has_otp)

    def present(self, primary, has_otp):
        current = primary
        self.copy(current)
        while True:
            action = self.ui.copied(current, current.value, has_otp,
                                    self.request['display'], self.request['display_seconds'],
                                    can_password=primary.kind == 'password' and current.kind == 'otp')
            if action in ('done', 'cancel', None):
                break
            try:
                if action == 'alias':
                    try:
                        self.save_alias(primary)
                    except BridgeError as error:
                        self.manager.guard()
                        self.ui.message('别名未保存', str(error))
                    continue
                if action == 'password' and primary.kind == 'password':
                    # Re-read instead of reusing a password that may have changed.
                    primary = resolve_identity(self.fetch('password', primary.username), primary.identity)
                    current = primary
                elif action == 'otp' and primary.kind == 'password':
                    selected = self.fresh_code(primary)
                    if selected is None:
                        continue
                    current = selected
                elif action == 'refresh' and current.kind == 'otp':
                    current = self.fresh_code(primary, current.identity)
                else:
                    raise BridgeError('不支持的窗口操作。')
                self.copy(current)
            except CredentialLookupError as error:
                self.ui.message('未复制验证码', str(error))
            except AliasExists as error:
                self.ui.message('别名未保存', str(error))
        message = '密码和验证码已分别复制。' if self.did_password and self.did_otp else ('密码已复制。' if self.did_password else '验证码已复制。')
        return {'ok': True, 'message': message}
