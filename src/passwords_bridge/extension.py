"""Pinned official extension archive, prepared explicitly and verified on every use.

CRX container parsing is informed by chauffeur 0.14.1 (MIT); see the notices.
The SHA-256 pin checks release-pinned bytes; it is not a CRX signature audit.
"""

import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import struct
import tempfile
import urllib.parse
import urllib.request
import zipfile

from .browser import BridgeError


EXTENSION_ID = "pejdijmoenmkgeppbflobdenhhabjlaj"
VERSION = "3.4.0"
SHA256 = "610ddd342cab1f66c317da9a24206839829a7ba10376afbb21c94781c3d84f26"
MAX_ARCHIVE = 4 * 1024 * 1024
NATIVE_MANIFEST = Path("/Library/Google/Chrome/NativeMessagingHosts/com.apple.passwordmanager.json")


def data_dir():
    root = Path.home() / "Library/Application Support/Passwords Quick Copy"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    return root


def archive_path():
    return data_dir() / ("icloud-passwords-" + VERSION + ".crx")


def verify(data):
    if len(data) > MAX_ARCHIVE or hashlib.sha256(data).hexdigest() != SHA256:
        raise BridgeError("扩展校验失败。当前版本不接受变更后的扩展；请更新 workflow 后重新准备。")
    return data


def setup():
    destination = archive_path()
    if destination.exists():
        verify(destination.read_bytes())
        return
    query = urllib.parse.urlencode({"response": "redirect", "prodversion": "150.0.0.0",
                                   "acceptformat": "crx3",
                                   "x": "id=" + EXTENSION_ID + "&installsource=ondemand&uc"})
    request = urllib.request.Request("https://clients2.google.com/service/update2/crx?" + query,
                                     headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            if urllib.parse.urlsplit(response.url).scheme != "https":
                raise BridgeError("扩展下载未使用 HTTPS，已停止。")
            data = verify(response.read(MAX_ARCHIVE + 1))
    except OSError:
        raise BridgeError("无法下载已固定版本的官方扩展，请检查网络后重试 pwsetup。") from None
    descriptor, temporary = tempfile.mkstemp(prefix="extension-", dir=str(destination.parent))
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def extract(data, destination):
    verify(data)
    if data[:4] != b"Cr24" or len(data) < 12 or struct.unpack("<I", data[4:8])[0] != 3:
        raise BridgeError("扩展文件格式不受支持。")
    offset = 12 + struct.unpack("<I", data[8:12])[0]
    try:
        with zipfile.ZipFile(io.BytesIO(data[offset:])) as archive:
            if sum(item.file_size for item in archive.infolist()) > 16 * 1024 * 1024:
                raise BridgeError("扩展展开大小超过限制。")
            for item in archive.infolist():
                relative = PurePosixPath(item.filename)
                if relative.is_absolute() or ".." in relative.parts or stat.S_ISLNK(item.external_attr >> 16):
                    raise BridgeError("扩展包含不安全的文件路径。")
                if relative.parts and relative.parts[0] == "_metadata":
                    continue  # Chrome rejects store metadata when loading an unpacked copy.
                archive.extract(item, destination)
    except (ValueError, zipfile.BadZipFile):
        raise BridgeError("扩展压缩包无效。") from None
    manifest = json.loads((destination / "manifest.json").read_text())
    if manifest.get("version") != VERSION or manifest.get("background", {}).get("service_worker") != "background.js":
        raise BridgeError("扩展版本或入口与已验证版本不一致。")
    # The original Apple crypto is left intact. Only the small MIT bridge is appended.
    config = {"statusOk": 0, "statusInvalidParam": 2, "statusInvalidSession": 9,
              "statusServerError": 100, "nativeTimeoutMs": 25000, "wireUnpaired": "unpaired",
              "domain": "", "username": ""}
    readiness = r'''
globalThis.__passwordsNativeReady=false;
globalThis.__passwordsNativeGeneration=0;
{
  const originalConnectNative=chrome.runtime.connectNative.bind(chrome.runtime);
  chrome.runtime.connectNative=function(...args) {
    const port=originalConnectNative(...args);
    const generation=++globalThis.__passwordsNativeGeneration;
    globalThis.__passwordsNativeReady=false;
    port.onMessage.addListener((message) => {
      // Apple's listener is registered after this wrapper. Defer our observer
      // until it has reset/advanced SecretSession and parsed the native reply,
      // preserving the ordering used by the original appended bridge listener.
      queueMicrotask(() => {
        if (generation!==globalThis.__passwordsNativeGeneration) return;
        if (message && message.cmd===14) globalThis.__passwordsNativeReady=true;
        if (typeof globalThis.__passwordsBridgeNativeReply==="function")
          globalThis.__passwordsBridgeNativeReply(message);
      });
    });
    port.onDisconnect.addListener(() => {
      queueMicrotask(() => {
        if (generation===globalThis.__passwordsNativeGeneration) {
          globalThis.__passwordsNativeReady=false;
          if (typeof globalThis.__passwordsBridgeNativeReply==="function")
            globalThis.__passwordsBridgeNativeReply({__passwordsNativeDisconnected:true});
        }
      });
    });
    return port;
  };
}
'''
    background = destination / "background.js"
    bridge = (Path(__file__).parent / "apw_bridge.js").read_text()
    background.write_text("globalThis.__passwordsConfig=" + json.dumps(config) + ";\n" + readiness +
                          background.read_text() + "\n" + bridge)


def prepare(destination):
    archive = archive_path()
    if not archive.exists():
        raise BridgeError("请先在 Alfred 运行 pwsetup，准备已固定版本的官方扩展。")
    extract(archive.read_bytes(), Path(destination))
