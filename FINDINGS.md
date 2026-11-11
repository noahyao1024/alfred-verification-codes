# Earlier Passwords UI automation findings

Update for 0.8.1: short names now have explicit domain-completion/original-host
query actions. OTP discovery uses the native metadata-only command; value reads
are deferred until needed. A pairing event in our log does not count or explain
all macOS Touch ID prompts, so no one-prompt guarantee is implied.

Update for 0.8.0: the unified `pass` entry now uses a native account picker,
metadata-only aliases, staged password/OTP copying, and optional transient
plaintext display for manual typing. These changes do not restore Passwords-app
UI automation or whole-vault enumeration. See the current README and SECURITY.md.

Update for 0.7.0: sessions are now reused for a bounded interval to avoid pairing
on every copy. Browser control remains on anonymous pipes. A token-protected,
copy-only local Unix socket is now used between Alfred and the bounded broker;
see [SECURITY.md](SECURITY.md) for the revised trust boundary.

Update for 0.6.0: the protocol bridge is now bundled under its MIT license.
Browser control uses inherited CDP pipes and each copy tears down its session;
the apwcli daemon and its TCP debugging endpoint are no longer used. See
[SECURITY.md](SECURITY.md) for verified boundaries and remaining limitations.

Update for 0.5.0: these are historical observations of the UI-scripting route,
not proof that all integration is impossible. `pass` and `pcode` now use
[apwcli](https://github.com/michel-tricot/apwcli), which communicates through
Apple's iCloud Passwords browser extension. This requires local pairing and a
supported browser; it does not rely on programmatic row selection.

This workflow began as an attempt to copy passwords directly out of Apple's
**Passwords** app. That attempt failed, and this file records the evidence so
the next person does not spend a day rediscovering it.

Tested on macOS 26.6.1 against an unlocked app with 1,376 entries.

## What works

Launching the app, waiting through the unlock, typing into the search field,
and the results filtering — confirmed by the window title reporting the match
count, e.g. `Searching “All” – 11 Items`.

## What does not

Selecting a result. Every *Copy…* command in the app is gated on it having a
current item, and nothing makes it register one.

| Approach | Result |
| --- | --- |
| `set selected of row to true` | Copy commands stay disabled |
| Arrow-key navigation from the search field | disabled |
| `perform action "AXPress"` on the row | disabled |
| Synthetic click at the row's screen coordinates | disabled |
| Row context menu via `AXShowMenu` | No menu exists anywhere in the accessibility tree |
| Menu bar *Edit* → *Copy Password* | Present and readable, never becomes enabled |
| Shortcuts / App Intents | The app vends none — no metadata, no intent strings in the binary |
| AppleScript dictionary | The app is not scriptable |
| `security` CLI | Data protection keychain has no command-line access |

The app accepts text input but not programmatic selection. That combination —
together with the context menu being absent from the accessibility tree
entirely — reads as deliberate hardening rather than an oversight.

## Traps worth knowing

**The first outline in the window is the sidebar.** Searching for an outline
that "has rows" returns the sidebar (All, Passkeys, Codes, Wi-Fi, Security,
Deleted), not the results list. Its row count stays at 5 no matter what you
search for, and selecting its rows silently changes the search scope — including
into a shared group, which then quietly limits every subsequent search.

**While locked, the app reports zero windows.** Authentication is handled by a
separate process (`LocalAuthenticationRemoteService`), so the app itself has no
window at all until you authenticate. A `window 1` reference captured on the
brief window that appears at launch goes stale, and every later lookup fails
with `-1719 Invalid index`.

**The app re-locks aggressively**, within a minute or two of losing focus, so
any automation has to survive re-authentication mid-run.

**Time your waits against the clock, not by adding up delays.** Each pass over
the accessibility tree is hundreds of Apple Events and costs far more than the
`delay` beside it. A loop that counts `0.2` per iteration and stops at 30 takes
over two minutes in practice. Michael Tsai has also
[documented AppleScript timeouts on Tahoe](https://mjtsai.com/blog/2025/09/17/tahoe-applescript-timeouts/)
where querying absent properties hangs for two minutes.

## Background

Apple's Developer Technical Support has
[confirmed](https://developer.apple.com/forums/thread/807782) there is no
command-line access to the data protection keychain, which is why
`security find-internet-password` cannot see these entries either.

The nearest prior art,
[alfred-icloud-passwords](https://github.com/leolabs/alfred-icloud-passwords),
takes a different route — it finds a button in the detail pane whose description
is `Password` and runs `AXShowMenu` on *that*, rather than on the row. But it
drives the old System Preferences pane rather than the standalone app, has been
unmaintained since 2023, and carries an unresolved "does not work on macOS
Ventura" issue.

## The conclusion at the time

Generating codes locally from a stored seed, which is what this workflow now
does, sidesteps the problem entirely for verification codes. For passwords
themselves, the attempts above did not find a working route. They did not test
the browser-extension bridge now used by apwcli.

If you find a way to make the app register a selection, the copy side was
written and worked up to exactly that point — see the git history for
`src/pw-copy.applescript`.
