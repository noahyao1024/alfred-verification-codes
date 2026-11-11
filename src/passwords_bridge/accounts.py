"""Account metadata used for selection and aliases. Secret values stay in memory."""

from dataclasses import dataclass, field
import json
from urllib.parse import urlsplit

from .browser import BridgeError
from .session import CredentialLookupError


def display_text(value, limit=300):
    return ''.join(c if ord(c) >= 32 else ' ' for c in str(value))[:limit]


def host(value):
    try:
        parsed = urlsplit(value if '://' in value else 'https://' + value)
        return (parsed.hostname or '').encode('idna').decode('ascii').lower().rstrip('.')
    except (ValueError, UnicodeError):
        return ''


def identity_key(identity):
    return json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


@dataclass
class Account:
    identity: dict
    value: str = field(repr=False)
    duplicate: bool = False

    @property
    def username(self):
        return self.identity['username']

    @property
    def kind(self):
        return self.identity['kind']

    @property
    def label(self):
        title = self.identity['title'] or self.username or '未命名账号'
        return display_text(title)

    @property
    def sites(self):
        return self.identity['sites']

    def row(self, index):
        suffix = ' · 标识重复，请先在「密码」中区分' if self.duplicate else ''
        return {'index': index, 'title': ('密码 · ' if self.kind == 'password' else '验证码 · ') + self.label + suffix,
                'username': display_text(self.username or '（无用户名）'),
                'sites': display_text(' · '.join(self.sites), 600),
                'kind': self.kind}


def account_list(entries, kind, domain, username='', metadata_only=False):
    result = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        user = entry.get('USR' if kind == 'password' else 'username', '')
        value = '' if metadata_only else entry.get('PWD' if kind == 'password' else 'code')
        if not isinstance(user, str) or (username and user != username):
            continue
        if not metadata_only and (not isinstance(value, str) or not value or value == 'Not Included'):
            continue
        if kind == 'password':
            sites = entry.get('sites') or [domain]
            title = entry.get('customTitle') or ''
        else:
            sites = [entry.get('domain') or domain]
            title = entry.get('source') or ''
        if not isinstance(sites, list) or not all(isinstance(site, str) for site in sites):
            continue
        identity = {'kind': kind, 'username': user, 'title': title if isinstance(title, str) else '',
                    'sites': sorted(set(sites))}
        result.append(Account(identity, value))
    counts = {}
    for item in result:
        key = identity_key(item.identity)
        counts[key] = counts.get(key, 0) + 1
    for item in result:
        item.duplicate = counts[identity_key(item.identity)] > 1
    return sorted(result, key=lambda item: (item.username.casefold(), item.label.casefold(), item.sites))


def resolve_identity(accounts, identity):
    matches = [item for item in accounts if item.identity == identity]
    if len(matches) != 1 or matches[0].duplicate:
        raise CredentialLookupError('绑定的账号已变化或存在同名重复项，请按网站重新选择并更新别名。')
    return matches[0]


def resolve_otp_metadata(accounts, identity):
    # Apple's metadata response can omit `source`, which supplies our title.
    # Ignore only that absent optional field; never ignore username/site or pick
    # among multiple matching factors. Stored full aliases still use exact identity.
    matches = [item for item in accounts if item.kind == 'otp' and
               item.username == identity['username'] and item.sites == identity['sites'] and
               (not identity['title'] or item.identity['title'] == identity['title'])]
    if len(matches) != 1 or matches[0].duplicate:
        raise CredentialLookupError('验证码账号已变化或存在多条匹配，请重新选择并核对。')
    return matches[0]


def otp_matches(password, codes):
    # No cross-account fallback. A different domain needs an explicit selection.
    same_user = [item for item in codes if item.username == password.username]
    sites = {host(site) for site in password.sites}
    exact = [item for item in same_user if any(host(site) in sites for site in item.sites)]
    return exact if exact else same_user
