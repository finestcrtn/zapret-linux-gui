#!/usr/bin/env bash
# Install the Zapret Linux GUI flatpak from a downloaded release asset.
set -e

if ! command -v flatpak >/dev/null 2>&1; then
    echo "flatpak is required but not installed." >&2
    echo "Install it first with your system package manager, then run this script again." >&2
    exit 1
fi

flatpak install --user -y ./zapret-gui.flatpak

echo
echo "Installed. Launch 'Zapret Control' from your app menu, or run:"
echo "  flatpak run io.github.zapretgui.ZapretGui"
echo
echo "On first launch the app downloads zapret and shows one admin prompt — that is expected."