"""Allowlisted, secret-free performance measurements and local summaries."""

import math
from pathlib import Path
import re
import time


PERF_PHASES = {
    'broker_startup': '会话服务启动',
    'browser_startup': '浏览器与扩展启动',
    'pairing': 'Apple 配对',
    'password_metadata': '密码账号元数据',
    'otp_metadata': '验证码账号元数据',
    'password_value': '密码读取',
    'otp_value': '验证码读取',
    'catalog_total': 'Alfred 结果就绪',
    'time_to_picker': '旧账号窗口就绪',
    'picker_interaction': '旧账号窗口选择',
    'request_total': '请求总耗时',
}
LIFECYCLE_EVENTS = {
    'alias_saved', 'apple_session_expired', 'browser_disconnected',
    'catalog_cache_hit', 'copied', 'copied_otp', 'copied_password',
    'idle_expired', 'manual_lock', 'maximum_lifetime',
    'otp_metadata_requested', 'otp_metadata_unavailable', 'otp_requested',
    'pair_requested', 'paired', 'password_cache_hit',
    'password_metadata_requested', 'password_metadata_unavailable',
    'password_requested', 'prewarmed', 'request_failed', 'screen_locked',
    'sleep_or_clock_gap', 'started', 'startup_or_service_failed', 'stopped',
}
_PERF = re.compile(r'^perf\|([a-z_]+)\|([0-9]+)$')
_LOG_PERF = re.compile(r'^[0-9]+ perf ([a-z_]+) ([0-9]+)$')


def performance_event(phase, started, clock=time.perf_counter):
    if phase not in PERF_PHASES:
        raise ValueError('unknown performance phase')
    milliseconds = max(0, int(round((clock() - started) * 1000)))
    return f'perf|{phase}|{milliseconds}'


def format_log_event(event):
    """Return a safe log payload, or None for any non-allowlisted shape."""
    match = _PERF.fullmatch(event)
    if match and match.group(1) in PERF_PHASES:
        return f'perf {match.group(1)} {int(match.group(2))}'
    if event in LIFECYCLE_EVENTS:
        return event
    return None


def read_stats(path, samples_per_phase=100):
    path = Path(path)
    lines = []
    for candidate in (path.with_name(path.name + '.1'), path):
        try:
            lines.extend(candidate.read_text(errors='replace').splitlines())
        except OSError:
            pass
    values = {}
    for line in lines:
        match = _LOG_PERF.fullmatch(line)
        if match and match.group(1) in PERF_PHASES:
            values.setdefault(match.group(1), []).append(int(match.group(2)))
    return {phase: samples[-samples_per_phase:] for phase, samples in values.items()}


def stats_menu(path):
    stats = read_stats(path)
    items = []
    for phase in PERF_PHASES:
        samples = stats.get(phase, [])
        if not samples:
            continue
        ordered = sorted(samples)
        average = round(sum(samples) / len(samples))
        p95 = ordered[max(0, math.ceil(len(ordered) * .95) - 1)]
        items.append({
            'title': f'{PERF_PHASES[phase]} · 平均 {average} ms · P95 {p95} ms',
            'subtitle': f'最近 {len(samples)} 次；只记录阶段名和整数毫秒',
            'valid': False,
            'icon': {'path': 'icons/stats.png'},
        })
    if not items:
        items.append({'title': '还没有性能样本',
                      'subtitle': '完成一次账号搜索或复制后，再输入 pass stats。',
                      'valid': False,
                      'icon': {'path': 'icons/stats.png'}})
    return {'items': items}
