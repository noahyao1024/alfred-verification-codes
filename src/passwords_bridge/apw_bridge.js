// Adapted from apwlib 0.1.2; Copyright (c) 2026 Michel Tricot, MIT.
// See THIRD_PARTY_NOTICES.md. Apple's SecretSession performs SRP/SMSG crypto.
// The parent calls this bridge through its private CDP pipe. Only pairing and
// the selected read operation are allowed; no shared browser debugging endpoint exists.

(function apwBridge() {
  "use strict";

  const CONFIG = globalThis.__passwordsConfig;
  const OK = CONFIG.statusOk;
  const INVALID_PARAM = CONFIG.statusInvalidParam;
  const INVALID_SESSION = CONFIG.statusInvalidSession;
  const SERVER_ERROR = CONFIG.statusServerError;
  const NATIVE_TIMEOUT_MS = CONFIG.nativeTimeoutMs;
  const UNPAIRED = CONFIG.wireUnpaired;

  let pending = new Map(); // cmd -> { resolve, timer }

  // Keep the worker alive: swallow stray errors/rejections from the extension's own
  // code rather than let the runtime tear it down (which would drop the pairing).
  self.addEventListener("error", (event) => event.preventDefault());
  self.addEventListener("unhandledrejection", (event) => event.preventDefault());

  // The live pairing state, read on demand. Reports the raw handshake state too: it
  // lets the client tell a collapsed handshake (back to NotInSession after a verify —
  // e.g. a wrong PIN) from one still in progress, so it can fail fast instead of waiting
  // out the pairing timeout.
  function readState() {
    const state = typeof g_theState !== "undefined" ? g_theState : null;
    // The official worker sends CmdHello as soon as it opens the native port.
    // Starting SRP before that reply can be lost when the hello handler resets
    // SecretSession back to NotInSession.
    const nativeReady = globalThis.__passwordsNativeReady === true;
    return { paired: state === "SessionKeySet" && nativeReady, state: state, nativeReady: nativeReady };
  }

  function clearPending(cmd) {
    if (cmd === undefined) {
      for (const entry of pending.values()) clearTimeout(entry.timer);
      pending.clear();
      return;
    }
    const entry = pending.get(cmd);
    if (entry) {
      clearTimeout(entry.timer);
      pending.delete(cmd);
    }
  }

  function pair(message) {
    try {
      // Pairing is synchronous within this worker task, so this closes the
      // readiness/state race between the Python-side poll and ChallengePIN/PINSet.
      if (globalThis.__passwordsNativeReady !== true) {
        return { status: INVALID_SESSION, error: "native helper not ready" };
      }
      if (message.pin == null) {
        if (typeof ChallengePIN !== "function") return { status: SERVER_ERROR, error: "pairing unavailable" };
        // ChallengePIN() only starts a handshake from NotInSession; from any other state it
        // is a silent no-op. So reset to NotInSession first from ANY active state — a parked
        // mid-handshake (ChallengeSent/MSG1Set) OR an existing pairing (SessionKeySet).
        // Resetting a live pairing matters: otherwise a re-pair shows no PIN dialog, and the
        // stale paired=true makes a wrong PIN look like success. A fresh challenge means a
        // wrong PIN genuinely fails.
        if (
          typeof resetTheSession === "function" &&
          typeof ContextState !== "undefined" &&
          typeof g_theState !== "undefined" &&
          g_theState !== ContextState.NotInSession
        ) {
          resetTheSession(ContextState.NotInSession);
        }
        ChallengePIN();
      } else {
        if (typeof PINSet !== "function") return { status: SERVER_ERROR, error: "pairing unavailable" };
        if (typeof g_theState === "undefined" || g_theState !== "MSG1Set") {
          return { status: INVALID_SESSION, error: "pairing state changed" };
        }
        PINSet(String(message.pin));
      }
      return { status: OK };
    } catch (error) {
      return { status: SERVER_ERROR, error: String(error) };
    }
  }

  // The private caller's command. Resolves to `{ data }` on success,
  // `{ status, error }` on failure — the same shape the client keys on.
  async function request(message) {
    if (!message || typeof message !== "object") return { status: INVALID_PARAM, error: "malformed request" };

    if (message.cmd === 2) return pair(message);

    // Only the domain/username selected for this one operation may be read.
    // Password writes, enumeration, arbitrary native commands, and other sites fail closed.
    const body = message.body || {};
    const passwordRead = CONFIG.operation === "password" && message.cmd === 5 && message.qid === "CmdGetPassword4LoginName" &&
      body.ACT === 2 && body.URL === CONFIG.domain && body.USR === CONFIG.username;
    const passwordList = (CONFIG.operation === "password_list" || CONFIG.operation === "catalog") &&
      message.cmd === 4 && message.qid === "CmdGetLoginNames4URL" &&
      body.ACT === 5 && body.URL === CONFIG.domain && message.url === CONFIG.domain &&
      Object.keys(body).length === 2;
    const otpRead = CONFIG.operation === "otp" && message.cmd === 17 && message.qid === "CmdDidFillOneTimeCode" &&
      body.ACT === 2 && body.TYPE === "oneTimeCodes" && Array.isArray(body.frameURLs) &&
      body.frameURLs.length === 1 && body.frameURLs[0] === "https://" + CONFIG.domain;
    const otpList = (CONFIG.operation === "otp_list" || CONFIG.operation === "catalog") &&
      message.cmd === 16 && message.qid === "CmdDidFillOneTimeCode" &&
      body.ACT === 5 && body.TYPE === "oneTimeCodes" && Array.isArray(body.frameURLs) &&
      body.frameURLs.length === 1 && body.frameURLs[0] === "https://" + CONFIG.domain;
    if (!CONFIG.domain || !(passwordRead || passwordList || otpRead || otpList))
      return { status: INVALID_PARAM, error: "operation not permitted" };

    // Validate before touching the crypto or native layers.
    if (
      typeof message.cmd !== "number" ||
      typeof message.qid !== "string" ||
      !message.body ||
      typeof message.body !== "object"
    ) {
      return { status: INVALID_PARAM, error: "malformed request" };
    }
    if (typeof g_secretSession === "undefined" || !g_secretSession) {
      return { status: INVALID_SESSION, error: "extension not ready" };
    }
    if (typeof g_nativeAppPort === "undefined" || !g_nativeAppPort) {
      return { status: INVALID_SESSION, error: "extension not ready" };
    }
    if (globalThis.__passwordsNativeReady !== true) {
      return { status: INVALID_SESSION, error: "native helper not ready" };
    }
    if (typeof g_theState === "undefined" || g_theState !== "SessionKeySet") {
      return { status: INVALID_SESSION, error: UNPAIRED };
    }
    if (pending.has(message.cmd)) {
      // Only distinct metadata commands (4 and 16) may overlap during catalog.
      return { status: SERVER_ERROR, error: "busy" };
    }

    let smsg;
    try {
      smsg = g_secretSession.createSMSG(JSON.stringify(message.body));
    } catch (error) {
      return { status: SERVER_ERROR, error: String(error) };
    }

    return await new Promise((resolve) => {
      pending.set(message.cmd, {
        resolve: resolve,
        timer: setTimeout(() => {
          const stuck = pending.get(message.cmd);
          clearPending(message.cmd);
          if (stuck) stuck.resolve({ status: SERVER_ERROR, error: "native helper timed out" });
        }, NATIVE_TIMEOUT_MS),
      });
      try {
        g_nativeAppPort.postMessage({
          cmd: message.cmd,
          tabId: message.tabId,
          frameId: message.frameId,
          url: message.url,
          payload: JSON.stringify({ QID: message.qid, SMSG: smsg }),
        });
      } catch (error) {
        clearPending(message.cmd);
        resolve({ status: SERVER_ERROR, error: String(error) });
      }
    });
  }

  // Runs on Apple's native-message callback; nothing here may throw.
  function reply(message) {
    if (!message || typeof message !== "object") return;
    if (message.__passwordsNativeDisconnected === true) {
      if (!pending.size) return;
      const waiters = Array.from(pending.values(), (entry) => entry.resolve);
      clearPending();
      for (const resolve of waiters) {
        resolve({ status: INVALID_SESSION, error: "native helper disconnected" });
      }
      return;
    }
    const entry = pending.get(message.cmd);
    if (!entry) return;
    const resolve = entry.resolve;
    clearPending(message.cmd);
    try {
      const data = message.payload
        ? JSON.parse(g_secretSession.parseSMSG(message.payload.SMSG))
        : { STATUS: typeof message.STATUS === "number" ? message.STATUS : OK };
      resolve({ data: data });
    } catch (error) {
      resolve({ status: SERVER_ERROR, error: String(error) });
    }
  }

  // These functions are reachable only within the extension worker. The owner
  // invokes them through the inherited pipe and clears scope after each read.
  globalThis.__passwordsBridgeNativeReply = reply;
  globalThis.__passwordsBridge = Object.freeze({state: readState, request: request});

  // The parent polls state while the bounded PIN dialog is open.

  // Ensure the native port exists. The generation-aware wrapper installed before
  // Apple's worker routes every current port's replies to the callback above.
  // Apple's code can throw here (e.g. no native host installed); requests re-check
  // the port per call, so a failure degrades to "extension not ready".
  try {
    if (typeof g_nativeAppPort === "undefined" || !g_nativeAppPort) {
      if (typeof connectToBackgroundNativeAppAndSetUpListeners === "function") {
        connectToBackgroundNativeAppAndSetUpListeners();
      }
    }
  } catch (_e) {
    /* ignore */
  }
})();
