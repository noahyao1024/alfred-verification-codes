#!/bin/bash
# Builds dist/Passwords Quick Copy.alfredworkflow from src/
set -e
cd "$(dirname "$0")"
NAME="Passwords Quick Copy"

# Verify the TOTP implementation against the RFC 6238 test vectors. A code
# generator that is subtly wrong produces plausible-looking numbers that never
# work, so this must not be allowed to ship unchecked.
python3 src/totp.py --selftest >/dev/null || { echo "TOTP selftest FAILED" >&2; exit 1; }
echo "TOTP selftest OK"
python3 -m unittest discover -s tests -v

python3 build_plist.py
mkdir -p dist
rm -f "dist/$NAME.alfredworkflow"
chmod +x src/copy-otp.sh src/add-otp.sh src/totp.py
rm -rf src/__pycache__
( cd src && zip -q -r "../dist/$NAME.alfredworkflow" . -x '.*' '__pycache__/*' '*/__pycache__/*' '*.pyc' )
echo "Built dist/$NAME.alfredworkflow"
