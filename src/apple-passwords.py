#!/usr/bin/env python3
"""Copy through a bounded local session; no credentials returned over IPC."""

import os
import json
from pathlib import Path
import signal
import subprocess
import sys
from urllib.parse import urlsplit


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # -I ignores user Python paths; only our bundled modules are added.
from passwords_bridge.browser import BridgeError
from passwords_bridge.client import request_catalog, request_copy, request_login, request_selected, request_warm, lock_session
from passwords_bridge.extension import data_dir, setup
from passwords_bridge.aliases import AliasStore
from passwords_bridge.alias_editor import AliasEditor
from passwords_bridge.local_ui import LocalUI
from passwords_bridge.menu import action_item, alias_item, catalog_menu, menu, validate_action, with_icon
from passwords_bridge.performance import stats_menu


def parse_query(query):
    parts = query.strip().split(maxsplit=1)
    if not parts:
        raise BridgeError("请输入关键词，例如 pass github；多个账号时可在后面加过滤词。")
    site = parts[0]
    try:
        parsed = urlsplit(site if "://" in site else "https://" + site)
        host = (parsed.hostname or "").encode("idna").decode("ascii")
        if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
            raise ValueError
        labels = host.rstrip(".").split(".")
        if not host or any(not label or len(label) > 63 or
                           not label[0].isalnum() or not label[-1].isalnum() or
                           any(not (c.isalnum() or c == "-") for c in label)
                           for label in labels):
            raise ValueError
    except (ValueError, UnicodeError):
        raise BridgeError("请输入有效的网站关键词、域名或 http/https 链接。") from None
    # Query arguments are never shell code. Copy requests use the remainder as an
    # exact username; Alfred catalog requests use it only as local filter text.
    return host.rstrip("."), parts[1].strip() if len(parts) == 2 else ""


def clear_seconds():
    try:
        seconds = int(os.environ.get("CLEAR_CLIPBOARD_AFTER", "45"))
        if not 0 <= seconds <= 86400:
            raise ValueError
        return seconds
    except ValueError:
        raise BridgeError("Clear clipboard after 必须为 0–86400 的整数；0 表示不自动清空。") from None


def idle_seconds():
    try:
        value = int(os.environ.get('SESSION_IDLE_SECONDS', '1800'))
        if not 30 <= value <= 1800:
            raise ValueError
        return value
    except ValueError:
        raise BridgeError('会话空闲时长必须为 30–1800 秒，默认 1800 秒。') from None


def display_settings():
    show = os.environ.get('SHOW_SECRETS', '0').strip().lower() in ('1', 'true', 'yes')
    try:
        seconds = int(os.environ.get('DISPLAY_SECONDS', '45'))
        if not 10 <= seconds <= 180: raise ValueError
    except ValueError:
        raise BridgeError('明文显示时间必须为 10–180 秒。') from None
    return show, seconds


def catalog_query(query):
    raw = query.strip()
    mode = 'password'
    if raw.startswith('codes '):
        mode, raw = 'otp', raw[6:].strip()
    if not raw:
        return None
    token = raw.split(maxsplit=1)[0]
    explicit_url = '://' in token
    # A plain Alfred keyword is shorthand for an ASCII website label. Do not
    # turn arbitrary Unicode text or punctuation into a surprising IDNA .com
    # query; ASCII domains and URLs still go through the strict parser below.
    if not token.isascii():
        return None
    if not explicit_url and '.' not in token:
        if (len(token) < 2 or not token[0].isalnum() or not token[-1].isalnum() or
                any(not (char.isalnum() or char == '-') for char in token)):
            return None
    try:
        domain, needle = parse_query(raw)
    except BridgeError:
        return None
    # Apple still exposes a website-scoped API, but the Alfred UX is one keyword
    # path: bare words are resolved to their common .com destination silently.
    if not explicit_url and '.' not in domain and domain != 'localhost':
        domain += '.com'
    return domain, needle, mode


def local_menu_query(query):
    value = query.strip()
    return (not value or value in {'setup', 'lock', 'help', 'alias', 'codes'} or
            value.startswith('alias '))


def _validate_catalog_reply(result):
    if (result.get('mode') not in ('password', 'otp') or
            not isinstance(result.get('accounts'), list) or
            type(result.get('fallback')) is not bool or
            ('has_otp' in result and type(result['has_otp']) is not bool) or
            ('paired_now' in result and type(result['paired_now']) is not bool)):
        raise BridgeError('账号列表响应格式无效。')


def _alias_matches(records, needle):
    matches = [record for record in records if needle.casefold() in
               (' '.join([record['name'], record['domain'],
                          record['identity']['username'], record['identity']['title']])).casefold()]
    exact = [record for record in matches if record['name'].casefold() == needle.casefold()]
    return exact + [record for record in matches if record not in exact]


def show_menu(query, store=None, standalone=False):
    """Search accounts/aliases to both copy and display a password on screen."""
    store = store or AliasStore()
    raw = query.strip()
    pw_records = [record for record in store.all() if record['identity']['kind'] == 'password']
    if not raw:
        items = [alias_item(record, display=True) for record in pw_records]
        items.append(with_icon({
            'title': '输入网站或别名以显示密码',
            'subtitle': '例如 github · 回车复制密码并打开明文浮窗，方便对照手打',
            'valid': False,
        }, 'show'))
        return {'items': items}
    if raw.startswith('@'):
        matches = _alias_matches(pw_records, raw.lstrip('@'))
        if matches:
            return {'items': [alias_item(record, display=True) for record in matches]}
        return {'items': [with_icon({'title': '没有匹配的密码别名',
                                     'subtitle': '验证码别名不支持明文展示',
                                     'valid': False}, 'alias')]}
    if raw == 'codes' or raw.startswith('codes '):
        return {'items': [with_icon({'title': '验证码不支持明文浮窗',
                                     'subtitle': '请使用 pass codes 或 pcode 直接复制验证码',
                                     'valid': False}, 'otp')]}
    candidate = raw.casefold()
    names = [record['name'].casefold() for record in pw_records]
    if candidate and any(name.startswith(candidate) for name in names):
        items = [alias_item(record, display=True) for record in _alias_matches(pw_records, raw)]
        if candidate not in names:
            parsed = catalog_query(raw)
            if parsed is not None and parsed[2] == 'password':
                search_action = ('search_show', parsed[0]) if standalone else ('search', 'show ' + parsed[0])
                items.append(action_item('查找网站 ' + parsed[0], '回车查询并显示这个网站的密码',
                                         search_action[0], icon='search', query=search_action[1]))
        return {'items': items}
    parsed = catalog_query(raw)
    if parsed is None:
        matches = _alias_matches(pw_records, raw)
        if matches:
            return {'items': [alias_item(record, display=True) for record in matches]}
        return {'items': [with_icon({'title': '继续输入',
                                     'subtitle': '请输入英文网站简称、域名、HTTP/HTTPS URL 或已保存别名',
                                     'valid': False}, 'show')]}
    domain, needle, _ = parsed
    result = request_catalog(domain, 'password', idle_seconds())
    if not result.get('ok'):
        response = {'items': [{'title': result.get('message', '账号读取失败。'),
                               'subtitle': 'Alfred 中未返回任何密码或验证码值。',
                               'valid': False}]}
        if '已有复制操作' in result.get('message', ''):
            response['rerun'] = .5
        return response
    _validate_catalog_reply(result)
    paired_now = result.get('paired_now', False)
    if (not result['accounts'] and result['mode'] == 'password' and
            raw and ' ' not in raw and '.' not in raw and '://' not in raw):
        response = {'items': [with_icon({
            'title': '没有找到网站「' + domain + '」',
            'subtitle': ('若「' + raw[:80] +
                         '」是条目名称：无网站条目不能由 Apple 接口读取，请先在「密码」中添加网站。'),
            'valid': False,
        }, 'search')]}
    elif result['mode'] != 'password':
        response = {'items': [with_icon({
            'title': '没有可显示的密码账号',
            'subtitle': '此网站只有验证码记录；可用 pass 或 pcode 直接复制验证码。',
            'valid': False,
        }, 'otp')]}
    else:
        response = catalog_menu(
            domain, result['accounts'], needle, 'password', False,
            has_otp=result.get('has_otp'), display=True)
    if paired_now:
        response['rerun'] = .2
        try:
            if standalone:
                open_alfred_search(query, keyword_var='showkeyword', default_keyword='pshow')
            else:
                open_alfred_search('show ' + query)
        except BridgeError:
            pass
    return response


def direct_menu(query, store=None):
    stripped = query.strip()
    if stripped == 'stats':
        return stats_menu(data_dir() / 'session.log')
    store = store or AliasStore()
    if stripped == 'show' or stripped.startswith('show '):
        return show_menu(stripped[4:], store, standalone=False)
    if local_menu_query(query) or stripped.startswith('@'):
        return menu(query, store)
    candidate = stripped.casefold()
    names = [record['name'].casefold() for record in store.all()]
    if candidate and any(name.startswith(candidate) for name in names):
        response = menu(query, store)
        if candidate not in names:
            # Alias prefixes stay local. An explicit domain still reaches the
            # website path, including when a saved alias has the same prefix.
            parsed = catalog_query(query)
            if parsed is not None:
                response['items'].append(action_item(
                    '查找网站 ' + parsed[0], '回车查询这个网站的账号',
                    'search', icon='search', query=parsed[0]))
        return response
    parsed = catalog_query(query)
    if parsed is None:
        return menu(query, store)
    domain, needle, mode = parsed
    result = request_catalog(domain, mode, idle_seconds())
    if not result.get('ok'):
        response = {'items': [{'title': result.get('message', '账号读取失败。'),
                               'subtitle': 'Alfred 中未返回任何密码或验证码值。',
                               'valid': False}]}
        if '已有复制操作' in result.get('message', ''):
            response['rerun'] = .5
        return response
    _validate_catalog_reply(result)
    paired_now = result.get('paired_now', False)
    bare_keyword = stripped
    if (not result['accounts'] and result['mode'] == 'password' and
            bare_keyword and ' ' not in bare_keyword and
            '.' not in bare_keyword and '://' not in bare_keyword):
        response = {'items': [with_icon({
            'title': '没有找到网站「' + domain + '」',
            'subtitle': ('若「' + bare_keyword[:80] +
                         '」是条目名称：无网站条目不能由 Apple 接口读取，请先在「密码」中添加网站。'),
            'valid': False,
        }, 'search')]}
    else:
        response = catalog_menu(
            domain, result['accounts'], needle, result['mode'], result['fallback'],
            has_otp=result.get('has_otp'))
    if paired_now:
        # Pairing temporarily takes focus away from Alfred. One bounded rerun
        # refreshes the original query; cached responses do not rerun again.
        response['rerun'] = .2
        try:
            open_alfred_search(query)
        except BridgeError:
            # If Alfred rejects the explicit reopen, its native rerun remains
            # available without turning a successful lookup into an error.
            pass
    return response


def start_prewarm():
    """Warm the owned browser without delaying Alfred or requesting credentials."""
    subprocess.Popen([sys.executable, '-I', str(HERE / 'apple-passwords.py'), '--warm'],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def open_alfred_search(query, keyword_var='passwordkeyword', default_keyword='pass'):
    keyword = os.environ.get(keyword_var, default_keyword).strip() or default_keyword
    script = '''on run argv
tell application id "com.runningwithcrayons.Alfred" to search (item 1 of argv)
end run'''
    try:
        result = subprocess.run(['/usr/bin/osascript', '-e', script, keyword + ' ' + query],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        raise BridgeError('无法重新打开 Alfred 搜索，请直接输入 ' + keyword + ' ' + query) from None
    if result.returncode:
        raise BridgeError('无法重新打开 Alfred 搜索，请直接输入 ' + keyword + ' ' + query)


def begin_alias_editor(binding):
    AliasEditor().begin(binding)
    open_alfred_search('alias save ')
    return True, ''


def handle_result(result):
    if result.get('ok') and 'alias_target' in result:
        return begin_alias_editor(result['alias_target'])
    return result['ok'], result['message']


def interactive(action):
    action = validate_action(action)
    kind = action['action']
    if kind == 'setup': return run('pair', '')
    if kind == 'lock': return run('lock', '')
    if kind == 'search':
        open_alfred_search(action['query'])
        return True, ''
    if kind == 'search_show':
        open_alfred_search(action['query'], keyword_var='showkeyword', default_keyword='pshow')
        return True, ''
    if kind == 'begin_alias':
        domain, remainder = parse_query(action['domain'])
        if remainder: raise BridgeError('账号网站无效。')
        return begin_alias_editor({'domain': domain, 'identity': action['identity']})
    if kind == 'save_alias_draft':
        AliasEditor().save(action['draft'], action['name'], action['replace'])
        return True, '别名已保存：' + action['name']
    if kind in ('select', 'select_otp'):
        domain, remainder = parse_query(action['domain'])
        if remainder:
            raise BridgeError('账号选择网站无效。')
        default_show, duration = display_settings()
        if kind == 'select_otp':
            result = request_selected(domain, action['identity'], clear_seconds(), idle_seconds(), False, duration, otp=True)
        else:
            show = action['display'] if 'display' in action else default_show
            result = request_selected(domain, action['identity'], clear_seconds(), idle_seconds(), show, duration)
        return handle_result(result)
    ui = LocalUI()
    if kind == 'help':
        ui.message('Passwords Quick Copy', 'pass 关键词：回车直接复制密码；⌘↩ 复制验证码；⌥↩ 设置别名。\npshow 关键词（或 pass show / ⌃↩）：复制并显示明文浮窗。\npass 别名：直接使用已保存账号。\npass alias：查看、重命名或删除别名。\npass lock：锁定会话。')
        return True, '帮助已关闭。'
    if kind in ('rename', 'remove'):
        store = AliasStore()
        if kind == 'remove':
            return True, '别名已删除。' if store.remove(action['name']) else '这个别名不存在。'
        new = action.get('new')
        if not new:
            open_alfred_search('alias rename ' + action['name'] + ' ')
            return True, ''
        store.rename(action['name'], new)
        return True, '别名已重命名。'
    if kind == 'prompt':
        query = ui.text('查找网站账号', '输入关键词，可在后面加账号过滤词，例如 github me@example.com。')
        if query is None: return True, '已取消查询。'
        action = {'action': 'login', 'query': query}
    alias = action.get('alias', '')
    domain, username = ('', '') if alias else parse_query(action['query'])
    default_show, duration = display_settings()
    show = False if kind == 'bind' else (action['display'] if 'display' in action else default_show)
    result = request_login(domain, username, alias, action.get('name', '') if kind == 'bind' else '',
                           clear_seconds(), idle_seconds(), show, duration)
    return handle_result(result)


def run(mode, query):
    if mode not in ("password", "otp", "pair", "lock"):
        raise BridgeError("不支持的操作。")
    if mode == "pair":
        setup()
        return True, "扩展已准备。首次复制时配对，会话内可连续复制；pwlock 可立即锁定。"
    if mode == 'lock':
        result = lock_session()
        if result['ok']: AliasEditor().discard()
        return result['ok'], result['message']
    site, username = parse_query(query)
    seconds = clear_seconds()
    result = request_copy(mode, site, username, seconds, idle_seconds())
    if not result['ok']:
        return False, result['message']
    noun = "密码" if mode == "password" else "验证码"
    suffix = f"；{seconds} 秒后在内容未改变时清空" if seconds else ""
    return True, f"{noun}已复制{suffix}。"


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    try:
        if args == ['--serve']:
            from passwords_bridge.broker import serve
            return serve()
        if args == ['--warm']:
            return 0 if request_warm(idle_seconds()).get('ok') else 1
        if args[:1] in (['--menu'], ['--show-menu']):
            query = args[1] if len(args) > 1 else ''
            if not query.strip():
                start_prewarm()
            try:
                result = (show_menu(query, standalone=True)
                          if args[0] == '--show-menu' else direct_menu(query))
            except BridgeError as error:
                result = {'items': [{'title': str(error), 'valid': False}]}
            except Exception:
                result = {'items': [{'title': '无法生成账号菜单，请检查别名文件。', 'valid': False}]}
            print(json.dumps(result, ensure_ascii=False))
            return 0
        if args[:1] == ['--act']:
            ok, message = interactive(json.loads(args[1]))
            print(message)
            return 0 if ok else 1
        if not args:
            raise BridgeError("用法：apple-passwords.py password|otp|pair|lock [网站域名 用户名]")
        ok, message = run(args[0], args[1] if len(args) > 1 else "")
    except BridgeError as exc:
        ok, message = False, str(exc)
    except KeyboardInterrupt:
        ok, message = False, "操作已取消。"
    except Exception:
        # No raw exception/response is allowed into Alfred's debugger.
        ok, message = False, "操作失败，未输出原始响应。请检查扩展准备状态和浏览器版本。"
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    def cancel(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, cancel)
    sys.exit(main())
