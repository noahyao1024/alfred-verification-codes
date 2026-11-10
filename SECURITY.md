# Security scope of version 0.7

Version 0.7 keeps pairing briefly so consecutive password/OTP copies do not each
require a PIN. This intentionally retains authorization longer than 0.6.
It is not an independently audited password manager or a cryptographic rewrite.

| Boundary | Version 0.7 behavior |
| --- | --- |
| Browser control | Inherited anonymous CDP pipes; no TCP debugging listener |
| Session lifetime | 5 minutes idle by default (30–1800 seconds configurable), at most 30 minutes total |
| Lock handling | Native screen-lock, session-inactive, and sleep notifications plus console-state checks; monitoring failure closes access |
| Client IPC | Copy/status/stop only; mode-0600 Unix socket and random per-session token in a mode-0700 directory; same OS user required |
| Credential delivery | Copied inside the broker; no plaintext credential response over the client socket |
| Operation scope | Validated type/domain/username per request; scope cleared between requests; no raw native command or evaluation endpoint |
| Manual control | `pwlock` cancels an in-flight operation and waits for shutdown |
| Idle accounting | Valid copy attempts refresh idle time; status and worker keepalive do not; active pairing is not interrupted by idle time |
| Extension | Exact pinned archive hash; explicit setup; no silent runtime replacement |
| Clipboard | Concealed/transient markers and generation-based timed clearing; cleanup retains no credential |

The broker serializes copies, rechecks lock/lifetime during browser waits and
before clipboard delivery, and clears operation scope afterward. A lookup that
needs a more specific username can retain pairing. Cancelled pairing, protocol
failure, or a disconnected browser discards that browser. Apple can invalidate
pairing independently; the next copy must pair again.

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

Logs contain fixed lifecycle event names and timestamps only. Session metadata
contains a PID and token, not Apple passwords or PINs. The original standalone
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
