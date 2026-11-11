# Passwords Quick Copy

An [Alfred](https://www.alfredapp.com) workflow that copies passwords and current
verification codes from Apple Passwords using a short-lived browser session.
The MIT protocol bridge is adapted from [apwlib 0.1.2](https://github.com/michel-tricot/apwcli/tree/v0.1.2);
the transport, lifecycle, operation restrictions, and clipboard integration are maintained here.
The existing `otp` / `otpadd` commands also support standalone seeds in your login keychain.

## Setup

Import `dist/Passwords Quick Copy.alfredworkflow`, then run `pwsetup` in Alfred
to download the pinned iCloud Passwords extension. No apwcli/chauffeur install
is required. This edition uses Python 3.9+ and its standard library (available
on this development Mac through Command Line Tools).

The first `pass` operation asks for Apple pairing. Enter the PIN shown
by macOS into the local dialog; subsequent password and OTP copies reuse that
session while it remains valid. By default, 5 minutes without a copy closes it.
Screen lock, sleep/user switching, `pwlock`, or a 30-minute maximum lifetime also
close the session. Apple can invalidate its pairing earlier, in which case the
next operation pairs again. This replaces 0.6's pairing on every copy.

This requires Alfred Powerpack and a supported Chromium browser (Chrome, Brave,
Edge, or Chromium) and Apple's browser helper. Setup accepts only the pinned
3.4.0 extension archive; a different store release fails verification instead
of silently changing executable code. It is a third-party bridge;
changes to Apple's extension can affect compatibility. No Apple Passwords CSV
export or duplicated OTP seed is required for `pass` / `pcode`.

For the standalone `otp` / `otpadd` commands, no setup changes:
seeds live in your login keychain, never in this workflow or repository.

Requires macOS Sequoia or later. No Accessibility permission is needed.

## Usage

Use **one entry point: `pass`**. Start with `pass github.com`; matching account
metadata appears directly in Alfred and remains searchable in its existing input.
Select an account and the password is copied first. Its value is
shown in a movable floating window for manual typing. When ready, click **Copy
Verification Code**; the matching code is fetched again and copied then, without
immediately overwriting the password after the first step. Choose **Show
verification-code-only accounts** to load those separately; when no password
exists, the workflow falls back to them automatically. A broader-domain or
multiple-code match asks you to select the code.

Short names also show query actions: `pass github` offers the visible completion
`github.com` plus an original-host option for single-label/intranet names. Matching
aliases keep priority; domains already present in aliases can also be suggested.
Other suffixes such as `.org` and `.io` can be typed directly. This is explicit
site completion, not a new whole-vault keyword search.

| Alfred input | Result |
| --- | --- |
| `pass` | Show saved aliases and actions; Enter on Find Account asks for a website |
| `pass github.com` | Show the site's password accounts in Alfred; type more to filter, then select and copy |
| `pass codes github.com` | Show verification-code accounts in Alfred |
| `pass github` | Choose a visible domain completion or query the original host |
| `pass github.com me@example.com` | Narrow the account list to this username |
| `pass gh-work` or `pass @gh-work` | Use the account bound to this alias |
| `pass alias` | List aliases; Command-Return renames, Option-Return deletes the alias |
| `pass alias add gh-work github.com [username]` | Select an account and save an alias without copying a secret |
| `pass alias rename gh-work gh-team` | Rename an alias |
| `pass alias remove gh-work` | Remove only the alias |
| `pass setup` | Prepare and verify the pinned official extension |
| `pass lock` | Close the current session without starting a new one |
| `pass stats` | Show recent average and P95 timings for each secret-free phase |
| `otp` / `otpadd` | Use the existing, separate local seed store |

The legacy `pcode`, `pwsetup`, and `pwlock` shortcuts remain compatible, but are
not needed for the new flow. A username is literal text (spaces are allowed);
do not add shell quotes. A full
http/https URL may replace the domain; only its hostname is used. Apple's helper matches
by registrable domain, so subdomains can return the same set of accounts. The
picker shows title, username, and returned sites rather than guessing which
record was intended. Records with identical visible identifiers are refused;
rename/distinguish them in Passwords first.

Use **Set Alias** in the copied-value window to save a selected account. Aliases
contain only its query domain, username, title, sites, and credential kind, never
a password or OTP seed. Password changes and result reordering do not change the
binding. A renamed/changed/duplicated account cannot silently redirect an alias:
query the domain again and update the binding. Overwriting a different binding
requires confirmation. `aliases.json` is private and updated atomically.

`pass` and `pcode` connect to a short-lived broker. It owns the private
browser profile and anonymous CDP pipes; no TCP debugging listener is opened.
The broker's Unix socket requires the same OS user and a per-session token in a
private directory. It returns account metadata for the Alfred result list, but
copies secrets within the broker and never returns passwords or codes to Alfred.
On explicit user request, the copied value is now
displayed in a temporary native window for manual typing; it is not put into
Notification Center history or debugging output. Turn off **Show copied value**
(`SHOW_SECRETS`) to suppress plaintext while retaining the copy controls. Set
`DISPLAY_SECONDS` (10–180, default 45) to change the window timeout. Screen viewers
can see the window. It closes when the operation is cancelled/locked or times out.
Credentials
receive clipboard privacy markers used by Alfred and other compliant history
managers; applications that ignore these markers may still record the clipboard.
The existing clear delay applies (45 seconds by default, `0` disables it).
The clipboard cleanup helper holds only a pasteboard change counter, never the secret;
a subsequent copy, even of identical text, is preserved.

Set `SESSION_IDLE_SECONDS` in Workflow Configuration to change the idle timeout
(30–1800 seconds, default 300). Keep-alive and status checks do not extend it.
Copies and active interactive flows refresh the idle timer; the 30-minute lifetime cap still applies. A typo
or ambiguous account can be corrected within the same pairing session.

These commands read Apple Passwords; they do not list the whole vault or edit
credentials. The original local-seed commands below remain separate.

**Add an account.** Copy its secret or `otpauth://` link, then use the `otpadd`
keyword. Give it a name, or leave it blank to take the name from the link. Your
clipboard is cleared once the seed is stored.

![Adding an account with the otpadd keyword](images/otpadd.png)

The seed is taken from the clipboard rather than typed after the keyword on
purpose: Alfred keeps a history of what you type into it, and a TOTP seed does
not belong in a searchable log.

**Copy a code.** Use the `otp` keyword and pick the account.

* <kbd>↩</kbd> Copy the current code.
* <kbd>⌥</kbd><kbd>↩</kbd> Forget this account.

The clipboard is cleared after the delay set in the Workflow's Configuration,
but only if it still holds the copied value — compared by SHA-256, so the code
is never held in a shell variable.

Codes are generated locally, so this works offline and never opens the Passwords
app. The implementation is
[RFC 6238](https://datatracker.ietf.org/doc/html/rfc6238), checked against that
document's published test vectors on every build, including non-default digit
counts, periods and hash algorithms.

## Command line

Everything above is doable from Alfred alone; these exist for scripting and
debugging, not because you need them.

```sh
python3 src/totp.py --store "you@example.com"   # add or replace one (stdin)
python3 src/totp.py --store                     # name taken from the URI
python3 src/totp.py --list                      # stored accounts
python3 src/totp.py --remove "you@example.com"  # forget one
python3 src/totp.py --selftest                  # RFC 6238 vectors
```

Seeds may be given either as a bare base32 secret or as a full `otpauth://` URI.
A URI is preferable where you have one: it carries the digit count, period and
algorithm, and those are honoured rather than assumed. Assuming the usual
6/30/SHA1 for an issuer that differs produces codes that look perfectly
plausible and never work.

## Security notes

Storing a seed outside your password manager means a second copy of your second
factor exists. If it sits on the same machine as the password it protects, the
two factors collapse into one device. That is true of any authenticator app on
your laptop, but it is worth deciding deliberately rather than by accident.

If an account belongs to an employer, check their security policy before moving
its seed into a personal tool.

## Development

```
src/     workflow source (info.plist, scripts, icon)
dist/    built .alfredworkflow package
```

```sh
./build.sh              # runs TOTP vectors and integration boundary tests, then packages
python3 build_plist.py  # regenerate info.plist only
```

After `pwsetup`, run `/usr/bin/python3 tests/check_native_bridge.py` for explicit
Chrome/extension checks. These verify pipe transport, absence of a TCP listener,
request restrictions, reuse of the actual browser with synthetic credentials,
native notification delivery, interruption, and teardown. The notification tests
use a private test notification instead of locking the user's screen. They do
not prompt for a PIN or retrieve credentials. Node, when installed, is
used only for the JavaScript policy development test; it is not a runtime dependency.

The build fails if the RFC 6238 vectors stop matching. That guard exists because
this shipped once with a generator that had never been verified.

## Troubleshooting

**Touch ID and pairing are different events.** The workflow's pairing log cannot
count macOS biometric prompts. Initial Apple pairing and credential access may
require separate system confirmations; the workflow cannot promise one Touch ID
prompt or bypass the system's checks.

Version 0.8.3 keeps Alfred's input visible while account metadata loads and
renders the results directly in Alfred. The common path now requests only Apple's
metadata-only password listing (`cmd 4`, ghost search); it defers OTP metadata
(`cmd 16`) until requested, eliminating one serial helper call before password
results. Password aliases and selected Alfred results also skip metadata scans
and resolve their bound identity during the one selected value read. A 30-second
in-session metadata cache makes repeated filtering immediate.

The password value (`cmd 5`) is read only after selecting an account, and is
cached in broker memory for the bounded session so repeated copies or switching
back from OTP do not re-prompt Touch ID (use Refresh Password in the window to
force a re-read; `pwlock` or lock/idle expiry clears the cache). OTP values are
read only after the user requests them, once per copy/refresh. `pass stats`
summarizes recent average and P95 timings for broker/browser startup, pairing,
metadata/value reads, Alfred-result readiness, the legacy picker, and total
requests. Logs contain only allowlisted phase/event names and integer
milliseconds—never domains, usernames, responses, or credential values.

If upgrading from 0.5, stop the old service once with `apwcli daemon stop`.
The new workflow neither connects to it nor restarts it. Its own bounded session
broker starts when account metadata or a credential is requested. Use `pwlock` to stop it. Setup and
runtime files live under `~/Library/Application Support/Passwords Quick Copy`:
the pinned extension, a mode-0600 `session.json` (PID/token while running), the
mode-0600 socket, and `session.log`. Logs contain only allowlisted events and
phase timings, never account names, domains, PINs, passwords, or request bodies.

On timeout/lock/shutdown, the broker closes its browser and removes the session
socket, token, and temporary browser profile. A force-killed process can leave
temporary files; closing its command pipe still makes Chrome exit. The monitor
exits if its broker parent dies. Stale runtime state is replaced under a singleton
lock on the next start. No login service is installed.

If setup reports a hash mismatch, do not bypass it: a compatible workflow update
is needed. Dismissing a PIN dialog cancels the copy and discards that browser
session. If startup is refused, unlock the Mac; the broker refuses to open a
session when the console is locked/inactive or lock monitoring is unavailable.
An uncertain connection result is not retried automatically; check the clipboard
before repeating the command.

With multiple accounts, append the username. With no verification code, check
that the matching Apple Passwords entry has a code configured. The new commands
never fall back to the standalone `otp` store.

## Prior history

This workflow started out trying to copy passwords directly from Apple's
Passwords app's UI. That approach failed; the observations are recorded in
[FINDINGS.md](FINDINGS.md). The new `pass` / `pcode` commands use the browser
extension bridge instead of UI scripting. Version 0.6 replaces the 0.5 apwcli
daemon integration with a restricted one-operation backend; 0.7 adds bounded
session reuse to avoid pairing for every copy. See
[使用说明.md](使用说明.md) and [SECURITY.md](SECURITY.md) for usage and the explicit limits.
Third-party code and licensing are recorded in [THIRD_PARTY_NOTICES.md](src/THIRD_PARTY_NOTICES.md).

## Licence

MIT — see [LICENSE](LICENSE).
