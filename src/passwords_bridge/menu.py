"""Alfred's single entry point. Menu generation never opens the password vault."""

import json

from .accounts import Account, display_text, validate_identity
from .aliases import AliasStore, alias_name
from .browser import BridgeError
from .alias_editor import AliasEditor


def action_item(title, subtitle, action, **values):
    return {'title': title, 'subtitle': subtitle, 'arg': json.dumps(dict(action=action, **values), ensure_ascii=False)}


def alias_item(record, management=False):
    identity = record['identity']
    item = action_item(record['name'], display_text(record['domain'] + ' · ' + identity['username'] + ' · ' + identity['title']),
                       'login', alias=record['name'])
    if management:
        item['mods'] = {
            'cmd': {'arg': json.dumps({'action': 'rename', 'name': record['name']}), 'subtitle': '重命名别名'},
            'alt': {'arg': json.dumps({'action': 'remove', 'name': record['name']}), 'subtitle': '删除别名（不删除密码）'},
        }
    else:
        item['mods'] = {
            'alt': {'arg': json.dumps({'action': 'rename', 'name': record['name']}), 'subtitle': '在 Alfred 中重命名别名'},
        }
        if identity['kind'] == 'password':
            item['mods']['cmd'] = {'arg': json.dumps({'action': 'select_otp', 'domain': record['domain'], 'identity': identity}),
                                   'subtitle': '直接复制这个账号的验证码，不弹出密码窗口'}
    return item


def catalog_menu(domain, records, needle='', mode='password', fallback=False):
    """Render secret-free account metadata directly in Alfred."""
    if mode not in ('password', 'otp'):
        raise BridgeError('账号列表类型无效。')
    terms = needle.casefold().split()
    items = []
    for record in records:
        if not isinstance(record, dict) or set(record) != {'identity', 'duplicate'} or type(record['duplicate']) is not bool:
            continue
        try:
            identity = validate_identity(record['identity'])
        except BridgeError:
            continue
        account = Account(identity, '', record['duplicate'])
        searchable = ' '.join([account.label, account.username, *account.sites,
                               '密码' if account.kind == 'password' else '验证码']).casefold()
        if not all(term in searchable for term in terms):
            continue
        row = account.row(len(items))
        item = {
            'title': ('复制密码 · ' if account.kind == 'password' else '复制验证码 · ') + account.label,
            'subtitle': ('⌘↩ 验证码 · ⌥↩ 别名  |  ' if account.kind == 'password' else '↩ 直接复制 · ⌥↩ 别名  |  ') + row['username'] + ' · ' + row['sites'],
            'match': searchable,
            'valid': not account.duplicate,
        }
        if not account.duplicate:
            item['arg'] = json.dumps({'action': 'select', 'domain': domain, 'identity': identity},
                                     ensure_ascii=False)
            item['mods'] = {'alt': {
                'arg': json.dumps({'action': 'begin_alias', 'domain': domain, 'identity': identity}, ensure_ascii=False),
                'subtitle': '在 Alfred 输入别名，不读取密码',
            }}
            if account.kind == 'password':
                item['mods']['cmd'] = {
                    'arg': json.dumps({'action': 'select_otp', 'domain': domain, 'identity': identity}, ensure_ascii=False),
                    'subtitle': '直接复制这个账号的验证码，不弹出密码窗口',
                }
        else:
            item['subtitle'] = '记录标识重复，请先在系统「密码」中区分。'
        items.append(item)
    if not items:
        items.append({
            'title': '没有匹配的密码账号' if mode == 'password' else '没有匹配的验证码账号',
            'subtitle': '可继续修改 Alfred 输入框中的搜索词。',
            'valid': False,
        })
    if mode == 'password':
        switch = ('codes ' + domain, '查找此网站的验证码',
                  '回车选择验证码账号；密码条目也可按 ⌘↩ 直接复制验证码')
        items.append(action_item(switch[1], switch[2], 'search', query=switch[0]))
    elif not fallback:
        items.append(action_item('返回密码列表', '回车选择要复制的密码',
                                 'search', query=domain))
    return {'items': items}


def alias_editor_menu(query, store, editor):
    draft = editor.current()
    if draft is None:
        return {'items': [{'title': '请先选择要设置别名的账号',
                           'subtitle': '在账号条目按 ⌥↩，或在密码窗口点击「设置别名」', 'valid': False}]}
    name = query[len('alias save'):].strip()
    binding = draft['binding']
    target = display_text(binding['domain'] + ' · ' + binding['identity']['username'] + ' · ' + binding['identity']['title'])
    if not name:
        return {'items': [{'title': '输入别名，例如 gh-work', 'subtitle': '绑定账号：' + target, 'valid': False}]}
    name = alias_name(name)
    existing = next((item for item in store.all() if item['name'].casefold() == name.casefold()), None)
    replace = bool(existing and any(existing[key] != binding[key] for key in ('domain', 'identity')))
    return {'items': [action_item(('更新已有别名 ' if replace else '保存别名 ') + name,
                                  '回车确认绑定：' + target, 'save_alias_draft',
                                  draft=draft['id'], name=name, replace=replace)]}


def menu(query, store=None, editor=None):
    store = store or AliasStore()
    query = query.strip()
    if query == 'alias save' or query.startswith('alias save '):
        return alias_editor_menu(query, store, editor or AliasEditor(store.root))
    if query == 'setup':
        return {'items': [action_item('准备密码扩展', '首次使用时准备并校验扩展', 'setup')]}
    if query == 'lock':
        return {'items': [action_item('锁定当前会话', '下次使用时重新配对', 'lock')]}
    if query == 'help':
        return {'items': [
            {'title': '输入 pass github 搜索账号', 'subtitle': '↩ 复制密码；⌘↩ 复制验证码；⌥↩ 设置别名', 'valid': False},
            action_item('管理别名', '查看、重命名或删除已保存的别名', 'search', query='alias'),
            action_item('首次使用：初始化', '准备密码扩展', 'setup'),
            action_item('性能统计', '查看各步骤耗时', 'search', query='stats'),
            action_item('锁定会话', '清除当前授权与内存缓存', 'lock'),
        ]}
    if query.startswith('alias add '):
        parts = query.split(maxsplit=3)
        if len(parts) != 4:
            return {'items': [{'title': '用法：pass alias add 别名 网站域名 [用户名]', 'valid': False}]}
        name = alias_name(parts[2])
        return {'items': [action_item('添加别名 ' + name, '查询账号并选择要绑定的条目', 'bind', name=name, query=parts[3])]}
    if query.startswith('alias remove '):
        name = alias_name(query.split(maxsplit=2)[2])
        return {'items': [action_item('删除别名 ' + name, '仅删除本地别名，不删除密码', 'remove', name=name)]}
    if query.startswith('alias rename '):
        parts = query.split()
        if len(parts) not in (3, 4):
            return {'items': [{'title': '用法：pass alias rename 旧别名 [新别名]', 'valid': False}]}
        values = {'name': alias_name(parts[2])}
        if len(parts) == 4: values['new'] = alias_name(parts[3])
        return {'items': [action_item('重命名别名 ' + values['name'], '保留原账号绑定', 'rename', **values)]}

    records = store.all()
    alias_mode = query == 'alias' or query.startswith('alias ')
    needle = query[5:].strip() if alias_mode else query.lstrip('@')
    matches = [record for record in records if needle.casefold() in
               (' '.join([record['name'], record['domain'], record['identity']['username'], record['identity']['title']])).casefold()]
    exact = [record for record in matches if record['name'].casefold() == needle.casefold()]
    matches = exact + [record for record in matches if record not in exact]
    items = [alias_item(record, management=alias_mode) for record in matches]
    if not query:
        items += [{'title': '输入网站或别名，例如 github', 'subtitle': '↩ 复制密码；⌘↩ 验证码；⌥↩ 设置别名', 'valid': False},
                  action_item('管理别名', '查看、重命名或删除别名', 'search', query='alias'),
                  action_item('锁定会话', '清除当前授权与内存缓存', 'lock'),
                  action_item('帮助与设置', '使用说明、初始化、性能统计', 'search', query='help')]
    elif not items:
        items = [{'title': '继续输入关键词', 'subtitle': '至少两个字符；无需区分短名称、域名或网址', 'valid': False}]
    if alias_mode:
        items.append({'title': '管理别名：回车使用，⌘回车重命名，⌥回车删除',
                      'subtitle': '添加：pass alias add 别名 网站域名；也可在复制弹窗中设置', 'valid': False})
    return {'items': items}


def validate_action(action):
    if not isinstance(action, dict) or not isinstance(action.get('action'), str):
        raise BridgeError('操作格式无效。')
    if action['action'] in ('select', 'select_otp', 'begin_alias'):
        if set(action) != {'action', 'domain', 'identity'} or not isinstance(action['domain'], str):
            raise BridgeError('账号选择操作格式无效。')
        action = dict(action)
        action['identity'] = validate_identity(action['identity'])
        return action
    if action['action'] == 'save_alias_draft':
        if (set(action) != {'action', 'draft', 'name', 'replace'} or
                not isinstance(action['draft'], str) or len(action['draft']) != 32 or
                type(action['replace']) is not bool):
            raise BridgeError('别名保存操作无效。')
        return dict(action, name=alias_name(action['name']))
    allowed = {
        'login': ({'action', 'query'}, {'action', 'alias'}),
        'bind': ({'action', 'name', 'query'},),
        'search': ({'action', 'query'},),
        'rename': ({'action', 'name'}, {'action', 'name', 'new'}),
        'remove': ({'action', 'name'},),
        'prompt': ({'action'},), 'setup': ({'action'},), 'lock': ({'action'},), 'help': ({'action'},),
    }
    if set(action) not in allowed.get(action['action'], ()) or any(not isinstance(value, str) for value in action.values()):
        raise BridgeError('操作格式无效。')
    return action
