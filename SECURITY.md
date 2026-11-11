# Security scope of version 0.8.5

This edition keeps pairing briefly so consecutive password/OTP copies do not each
require a PIN. This intentionally retains authorization longer than 0.6.
It is not an independently audited password manager or a cryptographic rewrite.

| Boundary | Version 0.8.5 behavior |
| --- | --- |
| Browser control | Inherited anonymous CDP pipes; no TCP debugging listener |
| Session lifetime | 30 minutes idle by default (30–1800 seconds configurable), at most 30 minutes total |
| Lock handling | Native screen-lock, session-inactive, and sleep notifications plus console-state checks; monitoring failure closes access |
| Client IPC | Warm/copy/login/catalog/select/status/stop only; mode-0600 Unix socket and random per-session token in a mode-0700 directory; same OS user required |
| Credential delivery | Account metadata may return for Alfred results; secrets are copied inside the broker and never returned over the client socket |
| Operation scope | Validated type/domain/username per request; scope cleared between requests; no raw native command or evaluation endpoint |
| Manual control | `pwlock` cancels an in-flight operation and waits for shutdown |
| Idle accounting | Valid operations refresh idle time; prewarming an existing session, status, and worker keepalive do not; active pairing is not interrupted by idle time |
| Extension | Exact pinned archive hash; explicit setup; no silent runtime replacement |
| Clipboard | Concealed/transient markers and generation-based timed clearing; cleanup retains no credential |
| Account UI | Searchable Alfred results show username/title/sites; aliases and selected results bind exact metadata, not a result index or password hash |
| Manual-entry display | Explicitly requested plaintext appears in a transient native floating window; no Notification Center record; can be disabled |

The broker serializes copies, rechecks lock/lifetime during browser waits and
before clipboard delivery, and clears operation scope afterward. A lookup that
needs a more specific username can retain pairing. Cancelled pairing, protocol
failure, or a disconnected browser discards that browser. Apple can invalidate
pairing independently; the next copy must pair again.

Opening the empty `pass` menu may start the isolated browser and native helper
ahead of the first query. Prewarming does not pair, query account metadata, read
a credential, show a PIN prompt, or extend an existing session's idle deadline.
The unpaired browser remains subject to the same idle and absolute lifetime
bounds and is reused by the first explicit website query.

The extension waits for Apple's initial native `CmdHello` response before
starting SRP pairing, because that response resets the native SecretSession and
can otherwise erase an in-flight challenge. Native readiness is reset for every
new native port. A stalled challenge fails after 12 seconds rather than starting
a concurrent SRP exchange; PIN verification is never bypassed.

The default Alfred search queries password metadata first (`cmd 4` for login
names), which does not trigger macOS Touch ID, and returns that metadata—not
secret values—to Alfred's result list. OTP metadata (`cmd 16`) is deferred until
the user asks for verification-code accounts or presses the OTP control; it is
queried automatically only when no password account exists. Only the selected
account's password is read via `cmd 5` after selection (or immediately for a
bound password alias), and once read it is cached in broker memory for the
duration of the active session (cleared on idle expiry, screen lock, sleep,
session invalidation, or `pwlock`) so repeated copies of the same account or
switching back from OTP do not re-trigger Touch ID. A Refresh Password action in
the floating window forces a fresh `cmd 5` read when a password was rotated.
Binding an alias (`pass alias add`) or cancelling the picker performs zero
secret reads. OTP values remain time-dependent and are read on demand after
confirmation, once per copy/refresh. No biometric check is disabled.
OTP matching never falls back to another username; broader-domain or multiple
matches require explicit selection. Indistinguishable metadata duplicates are
refused, as the protocol does not provide a stable identifier we can rely on.

OTP metadata may omit the optional source/title field. That omission is tolerated
only when username and sites resolve to one factor; multiple matches still fail.
Existing nonempty source bindings remain specific.

Aliases store only whitelisted account metadata in a mode-0600 JSON file. They
contain no password, code, seed, or secret-derived identifier. Renamed or duplicated
bindings fail instead of guessing a different account; overwrite requires
confirmation. Alias names and account metadata intentionally appear in Alfred.

On the user's explicit request, copied secrets may be shown in a large native
window for manual typing. The value travels to that owned UI child over stdin,
not argv, environment, temporary files, or the Alfred IPC response. The child
returns only an action/index/alias text. Plaintext is not sent to Notification
Center. Display timeout, screen lock, and cancellation close the window; screen
observers or recording software can still see it. `SHOW_SECRETS` disables this
display, while retaining the copy/OTP controls. The interaction may keep a session
busy, but remains bounded by the broker's absolute lifetime cap.

The Unix token is a local capability, not proof of Alfred's application identity.
Another process running as the same macOS user and able to read that token can
request copies. File permissions, UID checks, and a token do not protect against
an attacker who controls the user account, modifies the workflow, reads process
memory, or observes the clipboard. This boundary is explicitly accepted when
choosing short-session convenience; it is not equivalent to 0.6's lack of a
client socket.

Lock checks and notifications are asynchronous, not an atomic OS transaction
with clipboard writes. Normal events are handled promptly; the implementation
does not claim zero latency. A loop heartbeat gap also discards the session after
suspend or unexpected process stalls. The notification process exits when its
broker parent dies. Broker exit closes Chrome's command pipe, which causes Chrome
to exit. Normal shutdown removes runtime socket/token and browser profile;
a force kill can leave temporary files. Startup is protected by a singleton lock.

Logs contain allowlisted lifecycle/performance phase names, timestamps, and
integer milliseconds only. They never contain domains, usernames, helper
responses, or secret values. `pass stats` computes local recent average/P95
summaries from those records. Session metadata contains a PID and token, not
Apple passwords or PINs. The original standalone
`otp`/`otpadd` keychain paths remain separate and are not covered by claims about
the Apple Passwords broker.

The official extension is still loaded unpacked with a bundled MIT bridge and
`--enable-unsafe-extension-debugging`, controlled over a pipe. Its digest records
bytes fetched from the official store over HTTPS; it does not independently
audit Apple's code or CRX signing chain. Browser networking has not been audited
exhaustively. Neither apwcli, chauffeur, nor MCP is a runtime dependency.

Tests use synthetic credentials/authentication and a private native notification
to exercise lock handling without locking the user's Mac. Actual Apple pairing
and authenticated credential copying remain user acceptance checks.

Source provenance: [THIRD_PARTY_NOTICES.md](src/THIRD_PARTY_NOTICES.md).
