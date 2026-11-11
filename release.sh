#!/bin/bash
# Creates the GitHub repo if needed, pushes, and publishes a release with the
# workflow attached. Requires the gh CLI: brew install gh && gh auth login
set -e
cd "$(dirname "$0")"

: "${GH_USER:?Set GH_USER to your GitHub username}"
: "${AUTHOR:?Set AUTHOR to your name}"
REPO="${REPO:-alfred-verification-codes}"
VERSION="${VERSION:-0.8.5}"
NAME="Passwords Quick Copy"
DESCRIPTION="Alfred workflow to copy Apple Passwords passwords and verification codes"

echo "Building $VERSION with GH_USER=$GH_USER AUTHOR=$AUTHOR"
# VERSION must be exported: build_plist.py reads it, and without it the bundle
# would claim a different version from the tag it ships under.
export GH_USER AUTHOR REPO VERSION
./build.sh

if [ ! -d .git ]; then
  git init -q
  git branch -M main
fi
git add -A
git diff --cached --quiet || git commit -q -m "Release $VERSION"

if ! git remote get-url origin >/dev/null 2>&1; then
  gh repo create "$REPO" --public --source=. --remote=origin \
    --description "$DESCRIPTION"
fi
git push -u origin main

gh release create "v$VERSION" "dist/$NAME.alfredworkflow" \
  --title "v$VERSION" \
  --notes "Copy Apple Passwords passwords and verification codes via Alfred.

Use pass setup to prepare the pinned official extension. No apwcli install is needed.
Opening pass prewarms the isolated browser without pairing or reading credentials.
Use pass with a website or alias, select the account, and copy the password first.
The native window can display the value for manual typing and copy a fresh OTP next.
Save aliases from the focused text field; pass alias lists and manages them.
Pairing is reused until 30 minutes idle, screen lock, pwlock, or 30 minutes total.
Browser control uses anonymous pipes with no TCP debugging listener.

The existing otp and otpadd local-seed commands remain available.
Download the .alfredworkflow file below and double-click to install."

echo
echo "Done: https://github.com/$GH_USER/$REPO"
