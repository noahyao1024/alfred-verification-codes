#!/usr/bin/env python3
"""Copy Apple Passwords credentials through apwcli without logging its output."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import urlsplit


HERE = Path(__file__).resolve().parent
INSTALL = "请先安装：brew install michel-tricot/tap/apwcli"


def cli_path():
    configured = os.environ.get("APWCLI_PATH", "").strip()
    candidates = [configured] if configured else [
        "/opt/homebrew/bin/apwcli", "/usr/local/bin/apwcli",
        str(Path.home() / ".local/bin/apwcli"), shutil.which("apwcli"),
    ]
    for path in candidates:
        if path and Path(path).is_file() and os.access(path, os.X_OK):
            return str(Path(path).resolve())
    raise ValueError(INSTALL)


def parse_query(query):
    parts = query.strip().split(maxsplit=1)
    if not parts:
        raise ValueError("请输入网站域名，例如 pass github.com；多个账号时在后面加用户名。")
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
        raise ValueError("请输入有效的网站域名或 http/https 链接。") from None
    # Query arguments are never shell code. The remainder is one exact username.
    return host.rstrip("."), parts[1].strip() if len(parts) == 2 else ""


def clear_seconds():
    try:
        seconds = int(os.environ.get("CLEAR_CLIPBOARD_AFTER", "45"))
        if not 0 <= seconds <= 86400:
            raise ValueError
        return seconds
    except ValueError:
        raise ValueError("Clear clipboard after 必须为 0–86400 的整数；0 表示不自动清空。") from None


def backend_env():
    env = dict(os.environ)
    # apwcli invokes pbcopy by name. This shim writes clipboard privacy markers
    # atomically with the text, before clipboard-history apps can observe it.
    env["PATH"] = str(HERE / "clipboard-bin") + ":" + env.get("PATH", "/usr/bin:/bin")
    return env


def run(mode, query):
    if mode not in ("password", "otp", "pair"):
        raise ValueError("不支持的操作。")
    if mode == "pair":
        command = [cli_path(), "daemon", "pair"]
    else:
        site, username = parse_query(query)
        seconds = clear_seconds()
        command = [cli_path(), "pw" if mode == "password" else "otp", "get",
                   "--clipboard", "--clear-after", str(seconds), "--", site]
        if username:
            command.append(username)
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=backend_env(), timeout=180)
    except subprocess.TimeoutExpired:
        return False, "等待超时。请在本机完成 Apple 配对后重试，或运行 apwcli doctor 检查连接。"
    except OSError:
        return False, "无法启动 apwcli。请检查 Workflow Configuration 中的 APWCLI_PATH。"

    # Never forward backend output, exception strings, usernames, or credentials
    # to Alfred's debugger/notifications. Match only known error categories.
    if result.returncode:
        error = (result.stdout + result.stderr).lower()
        if b"multiple matches" in error:
            keyword = "pass" if mode == "password" else "pcode"
            return False, f"匹配到多个账号，尚未复制。请补充用户名：{keyword} 网站域名 用户名"
        if result.returncode == 3 or b"no password to copy" in error or b"no one-time code to copy" in error:
            return False, "未找到可复制的条目。请检查网站、用户名，以及是否已在「密码」中设置验证码。"
        if result.returncode == 9:
            return False, "尚未配对或配对已失效。请用 pwsetup 完成本机配对，再重试。"
        return False, "复制失败。请运行 apwcli doctor 检查浏览器扩展和 Apple Passwords 连接。"
    if mode == "pair":
        return True, "Apple Passwords 配对完成。可使用 pass 复制密码、pcode 复制验证码。"
    noun = "密码" if mode == "password" else "验证码"
    suffix = f"；{seconds} 秒后在内容未改变时清空" if seconds else ""
    return True, f"{noun}已复制{suffix}。"


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    try:
        if not args:
            raise ValueError("用法：apple-passwords.py password|otp|pair [网站域名 用户名]")
        ok, message = run(args[0], args[1] if len(args) > 1 else "")
    except ValueError as exc:
        ok, message = False, str(exc)
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
