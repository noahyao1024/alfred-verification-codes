# Security scope of version 0.6

This is a constrained integration based on MIT-licensed apwlib code, not a new
cryptographic implementation or an independently audited password manager.

| Boundary | Version 0.6 behavior |
| --- | --- |
| Password-service IPC | No daemon and no password-reading Unix socket |
| Browser control | Inherited anonymous CDP pipes; no TCP debugging port |
| Authorization lifetime | A fresh browser/pairing per copy; browser closes before clipboard write |
| Permitted operations | Pairing and one selected read type/domain/username; writes are rejected |
| Dependencies | Bundled protocol bridge plus Python standard library; no runtime apwcli/chauffeur/MCP |
| Extension updates | Explicit setup; exact SHA-256 pin checked on every run; mismatches fail closed |
| Clipboard cleanup | Concealed/transient markers; delayed clearing keeps only a change counter |

These changes reduce the specific exposure of a discoverable, already-paired
local service. They do not prove that this edition has fewer bugs overall.
Retaining Apple/apwlib protocol work avoids inventing new SRP or encryption code.

The browser still runs an unpacked copy of Apple's extension with a bridge
appended and `--enable-unsafe-extension-debugging`; the control channel is now
a private pipe. Credentials are necessarily plaintext in the owned processes
and clipboard. Clipboard markers are advisory. An attacker able to modify the
workflow/cache verification code, inspect process memory, or control the logged-in
macOS account is outside the protection claimed here. No OS-enforced application
identity boundary or sandboxed secrets broker is claimed.

The extension pin records bytes retrieved from the official Chrome Web Store
over HTTPS. It is not an independent verification of the CRX signing chain, an
audit of Apple's extension, or protection if both code and pin are modified.
The full browser's network behavior has not been exhaustively audited.

Normal success/failure/cancellation tears down the owned browser and temporary
profile. Closing the command pipe makes Chrome exit, verified locally; a force
kill can still leave temporary files on disk. Pairing must be repeated next time.
The original standalone `otp`/`otpadd` keychain implementation is retained and
is not covered by claims about the new Apple Passwords bridge.

Tests use synthetic secrets and unpaired extension sessions. Actual post-pairing
password/OTP retrieval remains a user acceptance check. Before recommending this
for high-value accounts, obtain independent review and perform those checks.

Source provenance and licensing: [THIRD_PARTY_NOTICES.md](src/THIRD_PARTY_NOTICES.md).
