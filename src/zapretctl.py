#!/usr/bin/env python3
"""zapretctl -- core engine for the ZapretGUI flatpak app.

This module does all the unprivileged work (downloads from GitHub, config,
site lists, strategy handling) and drives the host-side root daemon through
flatpak-spawn --host + systemctl/pkexec.

It NEVER touches an existing system zapret install: everything lives under
the app's own data dir (~/.var/app/<APP_ID>/data/zapret) and its own
systemd unit (zapret-gui.service) with its own nft table/queue names.
"""

import json
import os
import platform
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.request

APP_ID = "io.github.zapretgui.ZapretGui"
APP_VERSION = "0.1.0"

SERVICE_NAME = "zapret-gui"
SERVICE_UNIT = "zapret-gui.service"

# --- Zananya pinned components ------------------------------------------------
ZAPRET_REPO = "bol-van/zapret"
ZAPRET_VERSION = "v72.9"  # matches the recommended/tested version
STRAT_REPO = "Flowseal/zapret-discord-youtube"
STRAT_REV = "ef19845a801e4e743f7bdfdbd58f9745c6adbd60"  # pinned, same as tested
TOOL_REPO = "Sergeydigl3/zapret-discord-youtube-linux"

DEFAULT_STRATEGY = "general_alt11.bat"

# Unique operational identifiers for OUR copy (never collide with system zapret)
NFT_TABLE = "zapretgui"
NFT_QUEUE_NUM = "221"
IPT_CHAIN = "zapretgui"
IPT_CHAIN_REPLY = "replygui"
NFT_RULE_COMMENT = "Added by zapretgui"


# -----------------------------------------------------------------------------
# Paths (sandbox home is the host home, so sandbox path == host path)
# -----------------------------------------------------------------------------
def data_root():
    if os.environ.get("ZAPRETCTL_DATA_ROOT"):
        return os.environ["ZAPRETCTL_DATA_ROOT"]
    return os.path.normpath(os.path.join(os.path.expanduser("~/.var/app"),
                                         APP_ID, "data"))


def tool_dir():
    return os.path.join(data_root(), "zapret")


def conf_file():
    return os.path.join(tool_dir(), "conf.env")


def sites_file():
    return os.path.join(tool_dir(), "user-sites.txt")


def user_list_path():
    return os.path.join(tool_dir(), "user-lists", "list-general-user.txt")


def live_list_path():
    return os.path.join(tool_dir(), "zapret-latest", "lists",
                        "list-general-user.txt")


def strat_dir():
    return os.path.join(tool_dir(), "zapret-latest")


def custom_strat_dir():
    return os.path.join(tool_dir(), "custom-strategies")


def nfqws_path():
    return os.path.join(tool_dir(), "nfqws")


def install_log_path():
    return os.path.join(tool_dir(), "install.log")


def payload_env_path():
    return os.path.join(tool_dir(), "payload.env")


def host_installer_path():
    return os.path.join(tool_dir(), "host_installer.sh")


def host_uninstaller_path():
    return os.path.join(tool_dir(), "host_uninstaller.sh")


def daemon_sh_path():
    return os.path.join(tool_dir(), "daemon.sh")


# -----------------------------------------------------------------------------
# Host command execution
# -----------------------------------------------------------------------------
def in_sandbox():
    return os.path.exists("/.flatpak-info")


def run_host(cmd, timeout=60, check=True):
    """Run a command on the HOST (as the current user). Inside the sandbox we
    bridge with flatpak-spawn --host."""
    argv = list(cmd)
    if in_sandbox():
        argv = ["/usr/bin/flatpak-spawn", "--host", *argv]
    try:
        p = subprocess.run(argv, capture_output=True, text=True,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except FileNotFoundError:
        return -2, "", "command not found"
    err = (p.stderr or "").strip()
    if check and p.returncode != 0:
        raise RuntimeError(err or "command failed: " + " ".join(argv))
    return p.returncode, (p.stdout or "").strip(), err


def systemctl(*args, timeout=60):
    """Run `systemctl <args> zapret-gui.service` on the host."""
    return run_host(["systemctl", *args, SERVICE_UNIT], timeout=timeout,
                    check=False)


def service_state():
    rc, out, _ = run_host(["systemctl", "is-active", SERVICE_UNIT],
                          timeout=15, check=False)
    return "active" if rc == 0 and out.strip() == "active" else "inactive"


def service_installed():
    rc, _, _ = run_host(["systemctl", "list-unit-files", "--no-legend",
                         "--plain", SERVICE_UNIT], timeout=15, check=False)
    return rc == 0 and SERVICE_UNIT in _  # noqa: F841


def service_enabled():
    rc, out, _ = run_host(["systemctl", "is-enabled", SERVICE_UNIT],
                          timeout=15, check=False)
    return rc == 0 and "enabled" in out


# -----------------------------------------------------------------------------
# Downloads (stdlib only, works in the sandbox)
# -----------------------------------------------------------------------------
_UA = ("ZapretGUI/%(v)s (+flatpak) " % {"v": APP_VERSION}) + \
      "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"


def http_get(url, dest, timeout=120):
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA, "Accept": "*/*",
        "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)


def fetch_bytes(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_json(url, timeout=30):
    return json.loads(fetch_bytes(url, timeout=timeout))


def latest_zapret_tag():
    try:
        d = fetch_json("https://api.github.com/repos/%s/releases/latest"
                       % ZAPRET_REPO)
        return d.get("tag_name") or ZAPRET_VERSION
    except Exception:
        return ZAPRET_VERSION


def platform_dir():
    m = platform.machine().lower()
    table = {
        "x86_64": "linux-x86_64", "amd64": "linux-x86_64",
        "i686": "linux-x86", "i386": "linux-x86", "x86": "linux-x86",
        "armv7l": "linux-arm", "armv6l": "linux-arm",
        "aarch64": "linux-arm64", "arm64": "linux-arm64",
    }
    if m in table:
        return table[m]
    if m.startswith("mips64"):
        return "linux-mips64el" if m.endswith("el") else "linux-mips64"
    if m.startswith("mips"):
        return "linux-mipsel" if m.endswith("el") else "linux-mips"
    if m.startswith("ppc"):
        return "linux-ppc"
    raise RuntimeError("Unsupported architecture: " + m)


def download_nfqws(tag=None, dest=None):
    """Fetch the static nfqws binary from bol-van/zapret releases."""
    tag = tag or ZAPRET_VERSION
    dest = dest or nfqws_path()
    url = ("https://github.com/%s/releases/download/%s/zapret-%s.tar.gz"
           % (ZAPRET_REPO, tag, tag))
    tmp = tempfile.mkdtemp(prefix="zapret-nfqws-")
    try:
        archive = os.path.join(tmp, "zapret.tar.gz")
        http_get(url, archive)
        with tarfile.open(archive, "r:gz") as t:
            members = [m for m in t.getmembers()
                       if m.isfile()
                       and os.path.basename(m.name) == "nfqws"
                       and ("binaries/%s/" % platform_dir()) in m.name]
            if not members:
                raise RuntimeError(
                    "nfqws binary not found for platform %s" % platform_dir())
            t.extract(members[0], tmp)
            src = os.path.join(tmp, members[0].name)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(src, dest)
        os.chmod(dest, 0o755)
        return dest
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _transliterate(name):
    pairs = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
        "ё": "yo", "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k",
        "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
        "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts",
        "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
        "э": "e", "ю": "yu", "я": "ya",
        "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D", "Е": "E",
        "Ё": "Yo", "Ж": "Zh", "З": "Z", "И": "I", "Й": "Y", "К": "K",
        "Л": "L", "М": "M", "Н": "N", "О": "O", "П": "P", "Р": "R",
        "С": "S", "Т": "T", "У": "U", "Ф": "F", "Х": "H", "Ц": "Ts",
        "Ч": "Ch", "Ш": "Sh", "Щ": "Sch", "Ъ": "", "Ы": "Y", "Ь": "",
        "Э": "E", "Ю": "Yu", "Я": "Ya",
    }
    return "".join(pairs.get(ch, ch) for ch in name)


def rename_bat_entry(old):
    new = _transliterate(old)
    new = new.lower()
    new = re.sub(r"[\s()]+", "_", new)
    new = re.sub(r"__+", "_", new)
    new = re.sub(r"_+\.bat$", ".bat", new)
    return new


def download_strategies(rev=None, dest=None):
    """Fetch the Flowseal strategies repo, transliterate .bat names, return
    the dir containing general*.bat files."""
    rev = rev or STRAT_REV
    dest = dest or strat_dir()
    urls = ["https://codeload.github.com/%s/tar.gz/%s" % (STRAT_REPO, rev)]
    if rev != "refs/heads/main":
        urls.append("https://codeload.github.com/%s/tar.gz/refs/heads/main"
                    % STRAT_REPO)
    tmp = tempfile.mkdtemp(prefix="zapret-strat-")
    last_err = None
    try:
        archive = os.path.join(tmp, "strat.tar.gz")
        for url in urls:
            try:
                http_get(url, archive)
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
        else:
            raise RuntimeError("Could not download strategies: %s" % last_err)

        with tarfile.open(archive, "r:gz") as t:
            roots = {m.name.split("/", 1)[0] for m in t.getmembers()}
            root = sorted(roots)[0]
            t.extractall(tmp)
        # move to final dest and rename bats
        src = os.path.join(tmp, root)
        if os.path.exists(dest):
            shutil.rmtree(dest)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(src, dest)

        for fn in sorted(os.listdir(dest)):
            if fn.lower().endswith(".bat"):
                old = os.path.join(dest, fn)
                new = os.path.join(dest, rename_bat_entry(fn))
                if new != old and not os.path.exists(new):
                    os.replace(old, new)
        return dest
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def ensure_user_lists():
    """Create the user-lists and hardlink them into the strategy lists dir
    exactly like the upstream tool does (nfqws reads lists/)."""
    ul = os.path.join(tool_dir(), "user-lists")
    os.makedirs(ul, exist_ok=True)
    for fn in ("list-general-user.txt", "ipset-exclude-user.txt",
               "list-exclude-user.txt"):
        p = os.path.join(ul, fn)
        if not os.path.exists(p):
            open(p, "w").close()
        os.chmod(p, 0o644)
        live = os.path.join(strat_dir(), "lists", fn)
        try:
            if os.path.islink(live) or os.path.exists(live):
                os.remove(live)
            os.link(p, live)
        except OSError:
            shutil.copy2(p, live)
            os.chmod(live, 0o644)


# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
def detect_interface():
    try:
        with open("/proc/net/route") as f:
            for line in f:
                fields = line.split()
                if len(fields) >= 4 and fields[1] == "00000000":
                    iface = fields[0]
                    return iface if iface != "lo" else "any"
    except OSError:
        pass
    return "any"


def ensure_conf(interface=None):
    p = conf_file()
    strategy = DEFAULT_STRATEGY
    vals = {"interface": interface or detect_interface(),
            "gamefiltertcp": "false", "gamefilterudp": "false",
            "strategy": strategy, "firewall_backend": "auto"}
    if os.path.exists(p):
        with open(p) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k in vals:
                    vals[k] = v
        available = read_strategies()
        if vals["strategy"] not in available and available:
            vals["strategy"] = DEFAULT_STRATEGY if DEFAULT_STRATEGY in available \
                else available[0]
        if not vals["strategy"].endswith(".bat") and available:
            vals["strategy"] = available[0]
    if not vals["strategy"].endswith(".bat"):
        avail = read_strategies()
        vals["strategy"] = avail[0] if avail else "general.bat"
    with open(p, "w") as f:
        for k in ("interface", "gamefiltertcp", "gamefilterudp", "strategy",
                  "firewall_backend"):
            f.write("%s=%s\n" % (k, vals[k]))
    return p


def read_conf():
    vals = {"interface": "any", "gamefiltertcp": "false",
            "gamefilterudp": "false", "strategy": "", "firewall_backend": "auto"}
    p = conf_file()
    if os.path.exists(p):
        with open(p) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, _, v = line.partition("=")
                    if k in vals:
                        vals[k] = v
    return vals


def set_conf(**kw):
    vals = read_conf()
    for k, v in kw.items():
        if k in vals:
            vals[k] = v
    with open(conf_file(), "w") as f:
        for k in vals:
            f.write("%s=%s\n" % (k, vals[k]))
    return True


def current_strategy():
    return read_conf().get("strategy") or None


def set_strategy(name):
    if not name.endswith(".bat"):
        name += ".bat"
    return set_conf(strategy=name)


def read_strategies():
    names = []
    for d in (custom_strat_dir(), strat_dir()):
        if os.path.isdir(d):
            for fn in sorted(os.listdir(d)):
                if fn.endswith(".bat"):
                    names.append(fn)
    return sorted(set(names))


def read_sites():
    p = sites_file()
    if not os.path.exists(p):
        return []
    return [l.strip() for l in open(p) if l.strip()]


def write_sites(sites):
    os.makedirs(tool_dir(), exist_ok=True)
    with open(sites_file(), "w") as f:
        for s in sites:
            f.write(s + "\n")


def apply_sites_to_list(sites):
    os.makedirs(os.path.dirname(user_list_path()), exist_ok=True)
    with open(user_list_path(), "w") as f:
        for s in sites:
            f.write(s + "\n")
    try:
        os.remove(live_list_path())
    except FileNotFoundError:
        pass
    try:
        os.link(user_list_path(), live_list_path())
    except OSError:
        shutil.copy2(user_list_path(), live_list_path())


# -----------------------------------------------------------------------------
# Constants isolation patch (only touches OUR copy)
# -----------------------------------------------------------------------------
def patch_constants(constants_path=None):
    """Rewrite the tool's constants.sh so our daemon uses unique names."""
    constants_path = constants_path or os.path.join(tool_dir(), "src", "lib",
                                                    "constants.sh")
    if not os.path.exists(constants_path):
        return False
    with open(constants_path) as f:
        content = f.read()
    content = re.sub(r'^SERVICE_NAME=.*$', 'SERVICE_NAME="%s"' % SERVICE_NAME,
                     content, flags=re.M)
    content = re.sub(r'^NFT_TABLE=.*$', 'NFT_TABLE="inet %s"' % NFT_TABLE,
                     content, flags=re.M)
    content = re.sub(r'^NFT_QUEUE_NUM=.*$', 'NFT_QUEUE_NUM=%s' % NFT_QUEUE_NUM,
                     content, flags=re.M)
    content = re.sub(r'^NFT_RULE_COMMENT=.*$',
                     'NFT_RULE_COMMENT="%s"' % NFT_RULE_COMMENT,
                     content, flags=re.M)
    content = re.sub(r'^IPT_CHAIN=.*$', 'IPT_CHAIN="%s"' % IPT_CHAIN,
                     content, flags=re.M)
    content = re.sub(r'^IPT_CHAIN_REPLY=.*$',
                     'IPT_CHAIN_REPLY="%s"' % IPT_CHAIN_REPLY,
                     content, flags=re.M)
    with open(constants_path, "w") as f:
        f.write(content)
    return True


# -----------------------------------------------------------------------------
# Host scripts (written into the app data dir, run via pkexec / systemd)
# -----------------------------------------------------------------------------
DAEMON_SH = """#!/usr/bin/env bash
# Rev-{ver} ZapretGUI daemon wrapper.
set -e
DIR="$(realpath "$(dirname "$0")")"
HOME_DIR_PATH="$DIR"
REPO_DIR="$DIR/zapret-latest"
CUSTOM_STRATEGIES_DIR="$DIR/custom-strategies"
NFQWS_PATH="$DIR/nfqws"
CONF_FILE="$DIR/conf.env"
export PATH="$PATH:/usr/local/sbin:/usr/sbin:/sbin"
source "$DIR/src/lib/elevate.sh"
source "$DIR/src/lib/constants.sh"
source "$DIR/src/lib/common.sh"
source "$DIR/src/lib/firewall.sh"
source "$DIR/src/cli/run.sh"
case "${{1:-}}" in
  daemon) run_daemon ;;
  kill)   stop_zapret ;;
  status) check_nfqws_status ;;
  *) echo "usage: $0 daemon|kill|status"; exit 1 ;;
esac
"""

HOST_INSTALLER_SH = """#!/usr/bin/env bash
# ZapretGUI host installer -- run via: pkexec /usr/bin/bash $0
set -e
DIR="$(realpath "$(dirname "$0")")"
LOG="$DIR/install.log"
[ -f "$DIR/payload.env" ] && . "$DIR/payload.env"
ZAPRET_SERVICE="${{ZAPRET_SERVICE:-zapret-gui}}"
ZAPRET_USER="${{ZAPRET_USER:-${{SUDO_USER:-}}}}"
UNIT="/etc/systemd/system/${{ZAPRET_SERVICE}}.service"
POLKIT="/etc/polkit-1/rules.d/50-${{ZAPRET_SERVICE}}.rules"
mkdir -p /etc/polkit-1/rules.d
log() {{ echo "$(date '+%F %T') $*" >> "$LOG"; }}
log "=== install start service=$ZAPRET_SERVICE user=$ZAPRET_USER ==="
touch "$LOG"
chmod 644 "$LOG"

cat > "$UNIT" <<EOF
[Unit]
Description=Zapret GUI - DPI bypass daemon (flatpak-managed)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$DIR
User=root
ExecStart=/usr/bin/env bash $DIR/daemon.sh daemon
ExecStop=/usr/bin/env bash $DIR/daemon.sh kill
PIDFile=/run/${{ZAPRET_SERVICE}}.pid

[Install]
WantedBy=multi-user.target
EOF

cat > "$POLKIT" <<EOF
polkit.addRule(function(action, subject) {{
    if (subject.user === "$ZAPRET_USER" &&
        ((action.id === "org.freedesktop.systemd1.manage-units" &&
          action.lookup("unit") === "$ZAPRET_SERVICE.service") ||
         (action.id === "org.freedesktop.systemd1.manage-unit-files" &&
          action.lookup("unit") === "$ZAPRET_SERVICE.service") ||
         action.id === "org.freedesktop.systemd1.reload-daemon")) {{
        return polkit.Result.YES;
    }}
}});
EOF
chmod 644 "$POLKIT"

if [ -f "$DIR/nfqws" ]; then chmod 755 "$DIR/nfqws" || true; fi
chmod 755 "$DIR/daemon.sh" "$DIR/host_installer.sh" "$DIR/host_uninstaller.sh" || true

systemctl daemon-reload
systemctl enable "$ZAPRET_SERVICE" 2>>"$LOG" || true
systemctl restart "$ZAPRET_SERVICE" 2>>"$LOG" || true
sleep 3
log "install finished: $(systemctl is-active "$ZAPRET_SERVICE" 2>/dev/null)"
"""

HOST_UNINSTALLER_SH = """#!/usr/bin/env bash
# ZapretGUI host uninstaller -- run via: pkexec /usr/bin/bash $0
set -e
DIR="$(realpath "$(dirname "$0")")"
LOG="$DIR/install.log"
[ -f "$DIR/payload.env" ] && . "$DIR/payload.env"
ZAPRET_SERVICE="${{ZAPRET_SERVICE:-zapret-gui}}"
UNIT="/etc/systemd/system/${{ZAPRET_SERVICE}}.service"
POLKIT="/etc/polkit-1/rules.d/50-${{ZAPRET_SERVICE}}.rules"
log() {{ echo "$(date '+%F %T') $*" >> "$LOG"; }}
log "=== uninstall start service=$ZAPRET_SERVICE ==="
systemctl stop "$ZAPRET_SERVICE" 2>>"$LOG" || true
systemctl disable "$ZAPRET_SERVICE" 2>>"$LOG" || true
rm -f "$UNIT"
rm -f "$POLKIT"
systemctl daemon-reload
log "uninstall finished"
"""


def write_host_scripts():
    td = tool_dir()
    os.makedirs(td, exist_ok=True)
    files = [
        (daemon_sh_path(), DAEMON_SH),
        (host_installer_path(), HOST_INSTALLER_SH),
        (host_uninstaller_path(), HOST_UNINSTALLER_SH),
    ]
    for path, template in files:
        with open(path, "w") as f:
            f.write(template.format(ver=APP_VERSION))
        os.chmod(path, 0o755)
    # payload.env records who we are and the service name
    payload = "ZAPRET_SERVICE=%s\nZAPRET_USER=%s\n" % (
        SERVICE_NAME, _current_user())
    with open(payload_env_path(), "w") as f:
        f.write(payload)
    os.chmod(payload_env_path(), 0o644)


def _current_user():
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_name
    except Exception:  # noqa: BLE001
        return os.environ.get("USER", "root")


# -----------------------------------------------------------------------------
# High level: "download everything"
# -----------------------------------------------------------------------------
def is_ready():
    if not os.path.exists(nfqws_path()):
        return False
    if not read_strategies():
        return False
    if not os.path.exists(daemon_sh_path()):
        return False
    return True


def download_all(progress=None):
    """Fetch tool scaffold + nfqws + strategies and prepare the tool dir."""
    def report(msg):
        if progress:
            progress(msg)

    td = tool_dir()
    os.makedirs(td, exist_ok=True)
    os.makedirs(custom_strat_dir(), exist_ok=True)

    report("Fetching tool scaffold from GitHub…")
    _download_tool_scaffold(td)

    report("Patching constants for isolated operation…")
    patch_constants()

    report("Downloading nfqws binary…")
    download_nfqws()

    report("Downloading strategies…")
    download_strategies()
    ensure_user_lists()

    report("Writing config…")
    if not os.path.exists(conf_file()):
        ensure_conf()

    report("Writing host scripts…")
    write_host_scripts()

    report("Done.")
    return True


def _download_tool_scaffold(td):
    url = "https://codeload.github.com/%s/tar.gz/refs/heads/master" % TOOL_REPO
    tmp = tempfile.mkdtemp(prefix="zapret-tool-")
    try:
        archive = os.path.join(tmp, "tool.tar.gz")
        http_get(url, archive)
        with tarfile.open(archive, "r:gz") as t:
            root = sorted({m.name.split("/", 1)[0] for m in t.getmembers()})[0]
            t.extractall(tmp)
        src_root = os.path.join(tmp, root)
        # copy src/, auto-tune scripts, README -- mirror the deployed layout
        for item in ("src", "auto_tune.sh", "auto_tune_youtube.sh", "README.md"):
            s = os.path.join(src_root, item)
            d = os.path.join(td, item)
            if os.path.isdir(s):
                shutil.rmtree(d, ignore_errors=True)
                shutil.copytree(s, d)
            elif os.path.isfile(s):
                os.makedirs(os.path.dirname(d), exist_ok=True)
                shutil.copyfile(s, d)
                os.chmod(d, 0o755 if item.endswith(".sh") else 0o644)
        # rename_bat.sh needs to stay executable
        rn = os.path.join(td, "src", "rename_bat.sh")
        if os.path.exists(rn):
            os.chmod(rn, 0o755)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    import sys

    action = sys.argv[1] if len(sys.argv) > 1 else "download"
    if action == "download":
        download_all(lambda m: print("[zapretctl]", m))
    elif action == "detect-iface":
        print(detect_interface())
    elif action == "strategies":
        print("\n".join(read_strategies()))
    else:
        print("unknown action", action)
        sys.exit(2)