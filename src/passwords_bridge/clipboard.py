"""Write a protected pasteboard item, then keep only its change counter for cleanup."""

from pathlib import Path
import subprocess

from .browser import BridgeError


SCRIPT = Path(__file__).resolve().parents[1] / "secure-clipboard.js"


def copy_secret(value, seconds):
    result = subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", str(SCRIPT)],
                            input=value.encode("utf-8"), stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=15)
    counter = result.stdout.strip()
    if result.returncode or not counter.isdigit():
        raise BridgeError("剪贴板写入失败。")
    if seconds:
        # A bounded cleanup process owns no credential or pairing session. Its
        # only data is a change counter, and it cannot clear a subsequent copy.
        subprocess.Popen(["/bin/sh", "-c",
                          '/bin/sleep "$1"; exec /usr/bin/osascript -l JavaScript "$2" --clear-if "$3"',
                          "passwords-clipboard-clear", str(seconds), str(SCRIPT), counter.decode("ascii")],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
