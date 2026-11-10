import plistlib, os

HERE = os.path.dirname(os.path.abspath(__file__))

# Set these before publishing. GH_USER is used for the bundle ID, which must be
# unique and is conventionally reverse-domain based on the author.
GH_USER = os.environ.get("GH_USER", "noahyao1024")
AUTHOR = os.environ.get("AUTHOR", "Noah Yao")
REPO = os.environ.get("REPO", "alfred-verification-codes")
# Must track the release tag: Alfred shows this and uses it for update checks,
# so a bundle claiming one version inside a differently tagged release is a
# real mismatch.
VERSION = os.environ.get("VERSION", "0.6.0")
SRC = os.path.join(HERE, "src")

NOTIFY = "A1B2C3D4-0000-0000-0000-000000000006"
SF_OTP = "A1B2C3D4-0000-0000-0000-000000000009"
S_OTP = "A1B2C3D4-0000-0000-0000-00000000000A"
KW_ADD = "A1B2C3D4-0000-0000-0000-00000000000B"
S_ADD = "A1B2C3D4-0000-0000-0000-00000000000C"
S_RM = "A1B2C3D4-0000-0000-0000-00000000000D"
KW_PASSWORD = "A1B2C3D4-0000-0000-0000-00000000000E"
S_PASSWORD = "A1B2C3D4-0000-0000-0000-00000000000F"
KW_APP_OTP = "A1B2C3D4-0000-0000-0000-000000000010"
S_APP_OTP = "A1B2C3D4-0000-0000-0000-000000000011"
KW_PAIR = "A1B2C3D4-0000-0000-0000-000000000012"
S_PAIR = "A1B2C3D4-0000-0000-0000-000000000013"

OPT = 524288


def conn(dest, modifiers=0, subtext=""):
    return {"destinationuid": dest, "modifiers": modifiers,
            "modifiersubtext": subtext, "vitoclose": False}


def keyword(uid, kw, title, subtext, argumenttype=1):
    # argumenttype: 0 = required, 1 = optional, 2 = none.
    return {
        "config": {"argumenttype": argumenttype, "keyword": kw,
                   "subtext": subtext, "text": title, "withspace": True},
        "type": "alfred.workflow.input.keyword", "uid": uid, "version": 1,
    }


def scriptfilter(uid, kw, title, subtext, script):
    return {
        "config": {
            "alfredfiltersresults": True, "alfredfiltersresultsmatchmode": 0,
            "argumenttrimmode": 0, "argumenttype": 1, "escaping": 102,
            "keyword": kw, "queuedelaycustom": 3,
            "queuedelayimmediatelyinitially": True, "queuedelaymode": 0,
            "queuemode": 1, "runningsubtext": "", "script": script,
            "scriptargtype": 0, "scriptfile": "", "subtext": subtext,
            "title": title, "type": 0, "withspace": True,
        },
        "type": "alfred.workflow.input.scriptfilter", "uid": uid, "version": 3,
    }


def runscript(uid, script):
    # scriptargtype 1 is the one that passes the query as argv, so "$1" below
    # is populated. With 0 the script runs with no arguments at all, which is
    # how this shipped originally: every action saw an empty "$1".
    return {
        "config": {"concurrently": False, "escaping": 102, "script": script,
                   "scriptargtype": 1, "scriptfile": "", "type": 0},
        "type": "alfred.workflow.action.script", "uid": uid, "version": 2,
    }


objects = [
    # Keyword is driven by a configurable variable, per the Gallery style guide.
    scriptfilter(SF_OTP, "{var:otpkeyword}", "Verification codes",
                 "Copy a time-based code for a stored account",
                 "/usr/bin/python3 totp.py --alfred"),
    runscript(S_OTP, './copy-otp.sh "$1"'),
    runscript(S_RM, '/usr/bin/python3 totp.py --remove "$1"'),
    # Adding a seed has to be possible from Alfred alone: needing a terminal
    # to set the thing up is how a workflow gets abandoned before first use.
    # The seed comes off the clipboard rather than the keyword argument,
    # because Alfred keeps a history of what gets typed into it.
    keyword(KW_ADD, "{var:otpaddkeyword}", "Add a verification code",
            "Stores the secret or otpauth:// link on your clipboard"),
    runscript(S_ADD, './add-otp.sh "$1"'),
    {
        "config": {"lastpathcomponent": False, "onlyshowifquerypopulated": True,
                   "removeextension": False, "text": "{query}",
                   "title": "Passwords Quick Copy"},
        "type": "alfred.workflow.output.notification", "uid": NOTIFY,
        "version": 1,
    },
    keyword(KW_PASSWORD, "{var:passwordkeyword}", "复制 Apple Passwords 密码",
            "输入网站域名；多个账号时在后面加用户名", argumenttype=0),
    runscript(S_PASSWORD, '/usr/bin/python3 -I apple-passwords.py password "$1"'),
    keyword(KW_APP_OTP, "{var:appleotpkeyword}", "复制 Apple Passwords 验证码",
            "输入网站域名；验证码直接来自系统「密码」", argumenttype=0),
    runscript(S_APP_OTP, '/usr/bin/python3 -I apple-passwords.py otp "$1"'),
    keyword(KW_PAIR, "{var:pairkeyword}", "准备 Apple Passwords 扩展",
            "下载并校验固定版本；复制时再进行本次配对", argumenttype=2),
    runscript(S_PAIR, '/usr/bin/python3 -I apple-passwords.py pair'),
]

connections = {
    SF_OTP: [conn(S_OTP, 0, "copy code"),
             conn(S_RM, OPT, "forget this account")],
    S_OTP: [conn(NOTIFY)],
    S_RM: [conn(NOTIFY)],
    KW_ADD: [conn(S_ADD)],
    S_ADD: [conn(NOTIFY)],
    KW_PASSWORD: [conn(S_PASSWORD)],
    S_PASSWORD: [conn(NOTIFY)],
    KW_APP_OTP: [conn(S_APP_OTP)],
    S_APP_OTP: [conn(NOTIFY)],
    KW_PAIR: [conn(S_PAIR)],
    S_PAIR: [conn(NOTIFY)],
}

uidata = {
    SF_OTP: {"xpos": 40, "ypos": 40},
    S_OTP: {"xpos": 300, "ypos": 20},
    S_RM: {"xpos": 300, "ypos": 130},
    KW_ADD: {"xpos": 40, "ypos": 260},
    S_ADD: {"xpos": 300, "ypos": 260},
    NOTIFY: {"xpos": 560, "ypos": 140},
    KW_PASSWORD: {"xpos": 40, "ypos": 400},
    S_PASSWORD: {"xpos": 300, "ypos": 400},
    KW_APP_OTP: {"xpos": 40, "ypos": 540},
    S_APP_OTP: {"xpos": 300, "ypos": 540},
    KW_PAIR: {"xpos": 40, "ypos": 680},
    S_PAIR: {"xpos": 300, "ypos": 680},
}

# Gallery requires configuration to be exposed as Workflow Configuration.
userconfig = [
    {
        "config": {"default": "pass", "placeholder": "pass", "required": True, "trim": True},
        "description": "从系统「密码」复制密码：pass 网站域名 [用户名]。",
        "label": "Apple Passwords keyword", "type": "textfield", "variable": "passwordkeyword",
    },
    {
        "config": {"default": "pcode", "placeholder": "pcode", "required": True, "trim": True},
        "description": "从系统「密码」复制验证码：pcode 网站域名 [用户名]。",
        "label": "Apple verification code keyword", "type": "textfield", "variable": "appleotpkeyword",
    },
    {
        "config": {"default": "pwsetup", "placeholder": "pwsetup", "required": True, "trim": True},
        "description": "准备固定版本扩展。每次复制时完成独立 Apple 配对，结束后关闭会话。",
        "label": "Setup keyword", "type": "textfield", "variable": "pairkeyword",
    },
    {
        "config": {"default": "otp", "placeholder": "otp",
                   "required": True, "trim": True},
        "description": "The keyword used to copy a verification code.",
        "label": "Keyword", "type": "textfield", "variable": "otpkeyword",
    },
    {
        "config": {"default": "otpadd", "placeholder": "otpadd",
                   "required": True, "trim": True},
        "description": "The keyword used to store a new seed from the clipboard.",
        "label": "Add keyword", "type": "textfield", "variable": "otpaddkeyword",
    },
    {
        "config": {"default": "45", "placeholder": "45",
                   "required": False, "trim": True},
        "description": "Seconds before the clipboard is cleared, if it still "
                       "holds the copied value. Set to 0 to never clear.",
        "label": "Clear clipboard after", "type": "textfield",
        "variable": "CLEAR_CLIPBOARD_AFTER",
    },
]

readme = """## Setup

Run `pwsetup` once to download and verify the pinned Apple extension.
Requires Chrome, Brave, Edge, or Chromium. This edition uses bundled MIT \
bridge code with Python's standard library; apwcli and chauffeur are not runtime dependencies.

Requires macOS Sequoia or later. No Accessibility permission is needed.

## Usage

* `pass github.com [username]`: pair for this operation, then copy a password.
* `pcode github.com [username]`: pair for this operation, then copy its current code.
* If several accounts match, specify the username; nothing is copied until \
the match is unique. Enter usernames literally, without shell quotes.

The new commands do not expose credentials to Alfred's debugger or \
notifications. Clipboard privacy markers request exclusion from history.
Each operation owns a separate browser and anonymous CDP pipes. There is no \
daemon socket or TCP debugging listener. The browser closes before the secret \
is copied, so the next operation requires a new Apple pairing. This trades \
convenience for a shorter authorization lifetime.

The Apple extension is still modified to carry the bridge. A compromised Mac \
or modified workflow can still access secrets; this is not an independent security audit.

The existing standalone OTP generator needs no extension setup:

Copy a time-based verification code for a stored account via the `otp` keyword.

* <kbd>↩</kbd> Copy the current code.
* <kbd>⌥</kbd><kbd>↩</kbd> Forget this account.

Add an account by copying its secret or `otpauth://` link, then using the \
`otpadd` keyword. Give it a name, or leave it blank to take the name from the \
link. Your clipboard is cleared once the seed is stored.

The clipboard is cleared after the delay set in the Workflow’s Configuration, \
but only if it still holds the copied value.

The `otp` command generates codes locally from the stored seed (RFC 6238), \
independently of Apple Passwords. Use `pcode` for codes in Apple Passwords.
"""

wf = {
    # Bundle IDs are conventionally lowercase; GitHub usernames need not be.
    "bundleid": f"com.github.{GH_USER.lower()}.passwordsquickcopy",
    "category": "Productivity",
    "connections": connections,
    "createdby": AUTHOR,
    "description": "Copy passwords and verification codes from Apple Passwords; "
                   "also generate codes from existing local seeds.",
    "disabled": False,
    # The name Alfred shows. The bundle ID below deliberately still says
    # passwordsquickcopy: it is invisible to users, and changing it would make
    # Alfred treat this as a brand new workflow, leaving the old one installed
    # alongside it with its own configuration.
    "name": "Passwords Quick Copy",
    "objects": objects,
    "readme": readme,
    "uidata": uidata,
    "userconfigurationconfig": userconfig,
    "variables": {"otpkeyword": "otp", "otpaddkeyword": "otpadd",
                  "passwordkeyword": "pass", "appleotpkeyword": "pcode",
                  "pairkeyword": "pwsetup",
                  "CLEAR_CLIPBOARD_AFTER": "45"},
    "variablesdontexport": [],
    "version": VERSION,
    "webaddress": f"https://github.com/{GH_USER}/{REPO}",
}

path = os.path.join(SRC, "info.plist")
with open(path, "wb") as f:
    plistlib.dump(wf, f)

with open(path, "rb") as f:
    back = plistlib.load(f)

uids = {o["uid"] for o in back["objects"]}
for src, arr in back["connections"].items():
    assert src in uids, f"connection source {src} missing"
    for c in arr:
        assert c["destinationuid"] in uids, f"dest {c['destinationuid']} missing"
assert set(back["uidata"]) == uids, "uidata does not match objects"

# Gallery checks we can enforce ourselves.
declared = {c["variable"] for c in back["userconfigurationconfig"]}
assert "otpkeyword" in declared, "keyword must be user-configurable"
assert back["objects"][0]["config"]["keyword"] == "{var:otpkeyword}"
assert len(back["variables"]["otpkeyword"]) >= 3, "keyword must be >= 3 chars"
assert "otpaddkeyword" in declared, "add keyword must be user-configurable"
assert len(back["variables"]["otpaddkeyword"]) >= 3, "add keyword too short"
print("info.plist OK -", len(back["objects"]), "objects,",
      sum(len(v) for v in back["connections"].values()), "connections,",
      len(back["userconfigurationconfig"]), "config items")
