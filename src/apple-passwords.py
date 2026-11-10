#!/usr/bin/env python3
"""Copy through a bounded local session; no credentials returned over IPC."""

import os
from pathlib import Path
import signal
import sys
from urllib.parse import urlsplit


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # -I ignores user Python paths; only our bundled modules are added.
from passwords_bridge.browser import BridgeError
from passwords_bridge.client import request_copy, lock_session
from passwords_bridge.extension import setup


def parse_query(query):
    parts = query.strip().split(maxsplit=1)
    if not parts:
        raise BridgeError("请输入网站域名，例如 pass github.com；多个账号时在后面加用户名。")
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
        raise BridgeError("请输入有效的网站域名或 http/https 链接。") from None
    # Query arguments are never shell code. The remainder is one exact username.
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
        value = int(os.environ.get('SESSION_IDLE_SECONDS', '300'))
        if not 30 <= value <= 1800:
            raise ValueError
        return value
    except ValueError:
        raise BridgeError('会话空闲时长必须为 30–1800 秒，默认 300 秒。') from None


def run(mode, query):
    if mode not in ("password", "otp", "pair", "lock"):
        raise BridgeError("不支持的操作。")
    if mode == "pair":
        setup()
        return True, "扩展已准备。首次复制时配对，会话内可连续复制；pwlock 可立即锁定。"
    if mode == 'lock':
        result = lock_session()
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
