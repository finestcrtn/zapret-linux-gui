#!/usr/bin/env bash
# Build the ZapretGUI flatpak and produce a single-file bundle.
set -euo pipefail
cd "$(dirname "$0")"

MANIFEST=flatpak/io.github.zapretgui.ZapretGui.yaml

flatpak-builder --user --disable-rofiles-fuse --force-clean --repo=repo build "$MANIFEST"

flatpak build-update-repo --prune --prune-depth=1 repo

flatpak build-bundle repo zapret-gui.flatpak io.github.zapretgui.ZapretGui stable

echo
echo "Build finished."
echo "  local repo : $(pwd)/repo"
echo "  bundle     : $(pwd)/zapret-gui.flatpak"
echo
echo "Install with:  flatpak install        ./zapret-gui.flatpak"
echo "        or  :  flatpak install --user ./zapret-gui.flatpak"
echo "Run        :  flatpak run io.github.zapretgui.ZapretGui"