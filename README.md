# Zapret GUI — Flatpak edition

A self-contained Flatpak manager for the zapret DPI bypass stack. Downloads
everything from GitHub on first launch and runs through an isolated root
daemon. It never touches another zapret install on the machine.

## Install (recommended, click-to-install)

Release page: <https://github.com/finestcrtn/zapret-linux-gui/releases>

- Click **`zapretgui.flatpakref`** on the site/release to install via your
  software center, or run:
  ```sh
  flatpak install --user --from https://finestcrtn.github.io/zapret-linux-gui/zapretgui.flatpakref
  ```
- Or download **`zapret-gui.flatpak`** from the latest release and:
  ```sh
  flatpak install --user ./zapret-gui.flatpak
  ```
- Launch **Zapret Control** from your app menu
  (`flatpak run io.github.zapretgui.ZapretGui`).

Supported: mainstream *systemd* desktop Linux distros on x86_64. Requires
`nftables` or `iptables`, `systemd`, `pkexec`/polkit (all standard on
desktop distros). No python/tcl/tk/git/curl needed on the host.

## How it works

```
Flatpak sandbox (unprivileged GUI)
  ├── downloads nfqws + strategies + tool into ~/.var/app/io.github.zapretgui.ZapretGui/data/zapret
  └── controls the daemon via flatpak-spawn --host systemctl <cmd> zapret-gui.service
Host (created once by the one-time root step)
  ├── /etc/systemd/system/zapret-gui.service            (our own unit)
  └── /etc/polkit-1/rules.d/50-zapret-gui.rules          (silent start/stop for your user only)
```

First launch runs an automatic setup phase: the app shows **"Downloading
zapret…"** → **"Installing service…"** (the one-time admin password prompt) →
**"Starting zapret…"**, then switches to the normal layout. All later launches
go straight to the regular UI (status, Start/Stop, add site, strategy,
updates) — there are no setup buttons.

The daemon is set to start at boot by default. To change autostart or remove
the service from the command line:

```sh
systemctl disable zapret-gui            # stop auto-starting
pkexec /usr/bin/bash ~/.var/app/io.github.zapretgui.ZapretGui/data/zapret/host_uninstaller.sh   # remove service + policy rule
```

Switching back to this app after using a different zapret install: stop the
other one first (`systemctl stop zapret_discord_youtube`), then press **Start**
in the GUI, or `systemctl enable --now zapret-gui`. Never keep two zapret
daemons running at once.

Isolation from an existing install:

| | system install | this app |
|---|---|---|
| directory | `~/zapret-discord-youtube-linux` | `~/.var/app/io.github.zapretgui.ZapretGui/data` |
| unit | `zapret_discord_youtube.service` | `zapret-gui.service` |
| nft table / queue | `inet zapretunix` / 220 | `inet zapretgui` / 221 |

Only constraint: don't run both daemons **at the same time** (two nfqws queues
on the same traffic would fight). The app never stops, writes, or reads the
other install.

## Build

```sh
# one-time host deps
pkexec pacman -S --noconfirm flatpak-builder
flatpak install --user -y flathub org.freedesktop.Sdk//25.08

./build.sh
```

Produces:
- local repo: `repo/`
- single-file bundle: `zapret-gui.flatpak`

## Install / run

```sh
flatpak install --user ./zapret-gui.flatpak
flatpak run io.github.zapretgui.ZapretGui
```

## Layout

- `src/zapretctl.py` — core engine (downloads, config, sites, constants patch, host scripts)
- `src/zapret_gui.py` — tkinter GUI
- `flatpak/io.github.zapretgui.ZapretGui.yaml` — manifest (bundles Python+Tcl/Tk so the GUI has zero host deps)
- `tests/test_zapretctl.py` — host-side tests (no root needed)
- `build.sh` — build + bundle

## Version pinning

- nfqws: `bol-van/zapret` release `v72.9`
- strategies: `Flowseal/zapret-discord-youtube` pinned commit `ef19845a…` (falls back to `main`)
- tool scaffold: `Sergeydigl3/zapret-discord-youtube-linux` `master`

The "Check for updates" button re-runs the download and restarts the daemon.