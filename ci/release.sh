#!/usr/bin/env bash
# Build a signed flatpak repo, generate .flatpakrepo/.flatpakref and a single-
# file bundle. Runs on the CI runner (or locally).
#
# Usage: ci/release.sh KEYID VERSION REPO_DIR BUILD_DIR SITE_DIR OUT_DIR
set -euo pipefail

KEYID="$1"
VERSION="$2"
REPO_IN="$3"
BUILD_DIR="$4"
SITE_DIR="$5"
OUT="$6"

OWNER="${GITHUB_REPOSITORY_OWNER:-finestcrtn}"
PROJECT="zapret-linux-gui"
COLLECTION_ID="io.github.zapret-linux-gui.zapretgui"
APP_ID="io.github.zapretgui.ZapretGui"
BASE_URL="https://${OWNER}.github.io/${PROJECT}"

echo "== release.sh: version=$VERSION key=$KEYID owner=$OWNER =="

rm -rf "$OUT"
cp -r "$SITE_DIR" "$OUT"

# 1) single-file bundle for the GitHub Release asset
flatpak build-bundle "$REPO_IN" zapret-gui.flatpak "$APP_ID" stable
echo "  bundle: zapret-gui.flatpak"

# 2) public key (binary) + base64 copy for the ref files
gpg --batch --export "$KEYID" > "$OUT/zapretgui.gpg"
chmod 644 "$OUT/zapretgui.gpg"
PUB_B64="$(base64 -w0 "$OUT/zapretgui.gpg")"

# 3) resign + prune the repo with a stable collection-id / default branch
flatpak build-update-repo \
    --gpg-sign="$KEYID" \
    --gpg-homedir="$HOME/.gnupg" \
    --collection-id="$COLLECTION_ID" \
    --default-branch=stable \
    --prune \
    "$REPO_IN" >/dev/null
echo "  repo signed (collection-id=$COLLECTION_ID)"

# publish the ostree repo under /repo
mkdir -p "$OUT/repo"
cp -a "$REPO_IN"/. "$OUT/repo/"

cat > "$OUT/zapretgui.flatpakrepo" <<EOF
[Flatpak Repo]
Title=Zapret Linux GUI
Url=$BASE_URL/repo
Homepage=https://github.com/$OWNER/$PROJECT
Comment=DPI bypass manager (zapret) for Linux
Description=Downloads and manages zapret behind an isolated root daemon.
Icon=$BASE_URL/io.github.zapretgui.ZapretGui.svg
GPGKey=$PUB_B64
DefaultBranch=stable
EOF

cat > "$OUT/zapretgui.flatpakref" <<EOF
[Flatpak Ref]
Name=$APP_ID
Branch=stable
Title=Zapret Control (Linux GUI)
Comment=DPI bypass manager for Linux
Url=$BASE_URL/repo
IsRuntime=false
GPGKey=$PUB_B64
EOF

echo "  wrote zapretgui.flatpakrepo, zapretgui.flatpakref, zapretgui.gpg"
echo "== release.sh done =="