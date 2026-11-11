"""Alfred's single entry point. Menu generation never opens the password vault."""

import json

from .accounts import Account, display_text, validate_identity
from .aliases import AliasStore, alias_name
from .browser import BridgeError


def action_item(title, subtitle, action, **values):
    return {'title': title, 'subtitle': subtitle, 'arg': json.dumps(dict(action=action, **values), ensure_ascii=False)}


def alias_item(record):
    identity = record['identity']
    item = action_item(record['name'], display_text(record['domain'] + ' · ' + identity['username'] + ' · ' + identity['title']),
                       'login', alias=record['name'])
    item['mods'] = {
        'cmd': {'arg': json.dumps({'action': 'rename', 'name': record['name']}), 'subtitle': '重命名别名'},
        'alt': {'arg': json.dumps({'action': 'remove', 'name': record['name']}), 'subtitle': '删除别名（不删除密码）'},
    }
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
            'title': row['title'],
            'subtitle': row['username'] + ' · ' + row['sites'],
            'match': searchable,
            'valid': not account.duplicate,
        }
        if not account.duplicate:
            item['arg'] = json.dumps({'action': 'select', 'domain': domain, 'identity': identity},
                                     ensure_ascii=False)
        items.append(item)
    if not items:
        items.append({
            'title': '没有匹配的密码账号' if mode == 'password' else '没有匹配的验证码账号',
            'subtitle': '可继续修改 Alfred 输入框中的搜索词。',
            'valid': False,
        })
    if mode == 'password':
        switch = ('codes ' + domain, '显示仅验证码的账号',
                  '按需读取验证码元数据，不阻塞密码搜索结果')
        items.append(action_item(switch[1], switch[2], 'search', query=switch[0]))
    elif not fallback:
        items.append(action_item('返回密码账号', '密码账号使用更快的默认搜索',
                                 'search', query=domain))
    return {'items': items}


def menu(query, store=None):
    store = store or AliasStore()
    query = query.strip()
    if query == 'setup':
        return {'items': [action_item('准备密码扩展', '首次使用时准备并校验扩展', 'setup')]}
    if query == 'lock':
        return {'items': [action_item('锁定当前会话', '下次使用时重新配对', 'lock')]}
    if query == 'help':
        return {'items': [action_item('使用帮助', '账号选择、密码/验证码、别名和手动输入', 'help')]}
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
    items = [alias_item(record) for record in matches]
    if not alias_mode and query and not query.startswith('@'):
        parts = query.split(maxsplit=1)
        name, remainder = parts[0], (' ' + parts[1] if len(parts) == 2 else '')
        if '.' in name or '://' in name or ':' in name:
            items.insert(0, action_item('查询 ' + display_text(query), '直接在 Alfred 中显示账号', 'search', query=query))
        else:
            # Alias matches keep priority. Every inferred site is shown in full
            # before Enter; a short brand name is not a whole-vault search API.
            known = sorted({record['domain'] for record in records if name.casefold() in record['domain'].casefold()})
            domains = known + ([name + '.com'] if name + '.com' not in known and name != 'localhost' else [])
            for domain in domains:
                items.append(action_item('查询 ' + display_text(domain + remainder),
                    '已知网站' if domain in known else '短名称补全为 .com；其他后缀可直接输入完整域名',
                    'search', query=domain + remainder))
            items.append(action_item('查询原样名称 ' + display_text(query), '用于内网或单段主机名，不自动补后缀',
                                     'search', query='site ' + query))
    if not query:
        items += [action_item('查找网站账号…', '输入网站后显示可搜索的账号列表', 'prompt'),
                  action_item('准备扩展', '首次使用时运行一次', 'setup'),
                  action_item('锁定会话', '结束已配对会话', 'lock'),
                  action_item('使用与别名管理帮助', '也可输入 pass alias 查看已保存的别名', 'help')]
    elif not items:
        items = [{'title': '没有匹配的别名', 'subtitle': '输入完整网站域名查询，选中账号后可设置别名', 'valid': False}]
    if alias_mode:
        items.append({'title': '管理别名：回车使用，⌘回车重命名，⌥回车删除',
                      'subtitle': '添加：pass alias add 别名 网站域名；也可在复制弹窗中设置', 'valid': False})
    return {'items': items}


def validate_action(action):
    if not isinstance(action, dict) or not isinstance(action.get('action'), str):
        raise BridgeError('操作格式无效。')
    if action['action'] == 'select':
        if set(action) != {'action', 'domain', 'identity'} or not isinstance(action['domain'], str):
            raise BridgeError('账号选择操作格式无效。')
        action = dict(action)
        action['identity'] = validate_identity(action['identity'])
        return action
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
