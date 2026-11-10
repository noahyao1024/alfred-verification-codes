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

Each `pass` / `pcode` operation asks for a fresh Apple pairing. Enter the PIN
shown by macOS into the local dialog. The owned browser closes before the
credential is placed on the clipboard. This deliberately costs more interaction
than retaining a paired daemon between requests.

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

| Alfred input | Result |
| --- | --- |
| `pass github.com` | Copy the unique matching password from Apple Passwords |
| `pass github.com me@example.com` | Copy the password for this username |
| `pcode github.com` | Copy the unique matching current verification code |
| `pcode github.com me@example.com` | Copy the code for this username |
| `pwsetup` | Prepare and verify the pinned official extension |
| `otp` / `otpadd` | Use the existing, separate local seed store |

When more than one account matches, nothing is copied; add the username. The
username is literal text (spaces are allowed); do not add shell quotes. A full
http/https URL may replace the domain; only its hostname is used. Apple's helper matches
by registrable domain, so subdomains can return the same set of accounts.

`pass` and `pcode` use an inherited anonymous CDP pipe, a private temporary profile,
and a single paired operation. There is no password-service socket or TCP
debugging listener. Neither
passwords nor codes are returned to Alfred's debugger or notifications. They
receive clipboard privacy markers used by Alfred and other compliant history
managers; applications that ignore these markers may still record the clipboard.
The existing clear delay applies (45 seconds by default, `0` disables it).
The cleanup helper holds only a pasteboard change counter, never the secret;
a subsequent copy, even of identical text, is preserved.

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
rejection of writes/out-of-scope requests, unpaired rejection, and teardown.
They do not prompt for a PIN or retrieve credentials. Node, when installed, is
used only for the JavaScript policy development test; it is not a runtime dependency.

The build fails if the RFC 6238 vectors stop matching. That guard exists because
this shipped once with a generator that had never been verified.

## Troubleshooting

If upgrading from 0.5, stop the old service once with `apwcli daemon stop`.
The new workflow neither connects to it nor restarts it. Setup data is stored in
`~/Library/Application Support/Passwords Quick Copy`; only the pinned extension
archive is retained between normal runs. Cancelled/failed normal operations close
the browser and remove its temporary profile. A force-killed process can leave
temporary files, though closing its command pipe also makes Chrome exit.

If setup reports a hash mismatch, do not bypass it: a compatible workflow update
is needed. Dismissing a PIN dialog cancels the copy. A lost pairing requires
retrying the copy, not a persistent pairing setup step.

With multiple accounts, append the username. With no verification code, check
that the matching Apple Passwords entry has a code configured. The new commands
never fall back to the standalone `otp` store.

## Prior history

This workflow started out trying to copy passwords directly from Apple's
Passwords app's UI. That approach failed; the observations are recorded in
[FINDINGS.md](FINDINGS.md). The new `pass` / `pcode` commands use the browser
extension bridge instead of UI scripting. Version 0.6 replaces the 0.5 apwcli
daemon integration with a restricted one-operation backend. See
[使用说明.md](使用说明.md) and [SECURITY.md](SECURITY.md) for usage and the explicit limits.
Third-party code and licensing are recorded in [THIRD_PARTY_NOTICES.md](src/THIRD_PARTY_NOTICES.md).

## Licence

MIT — see [LICENSE](LICENSE).
