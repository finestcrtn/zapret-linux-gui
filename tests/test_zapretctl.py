#!/usr/bin/env python3
"""Host-side tests for zapretctl (no root required). Network tests hit GitHub.

Run:  python3 tests/test_zapretctl.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                "src"))
import zapretctl as zc  # noqa: E402

PASS = 0
FAIL = 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   %s%s" % (name, ("  " + extra if extra else "")))
    else:
        FAIL += 1
        print("  FAIL %s %s" % (name, extra))


def bash_n(path):
    r = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
    return r.returncode == 0, r.stderr.strip()


def main():
    print("== zapretctl tests ==")

    t = tempfile.mkdtemp(prefix="zapretctl-test-")
    os.environ["ZAPRETCTL_DATA_ROOT"] = t

    print("[transliteration]")
    ok("general (ALT11).bat -> general_alt11.bat",
       zc.rename_bat_entry("general (ALT11).bat") == "general_alt11.bat")
    ok("general (FAKE TLS AUTO ALT).bat -> general_fake_tls_auto_alt.bat",
       zc.rename_bat_entry("general (FAKE TLS AUTO ALT).bat")
       == "general_fake_tls_auto_alt.bat")
    ok("russian names transliterated",
       "ыэ" not in zc._transliterate("ыэ"))

    print("[constants patch]")
    td = os.path.join(t, "zapret")
    os.makedirs(os.path.join(td, "src", "lib"), exist_ok=True)
    cp = os.path.join(td, "src", "lib", "constants.sh")
    with open(cp, "w") as f:
        f.write(file_probe())
    zc.patch_constants(cp)
    content = open(cp).read()
    ok("SERVICE_NAME patched", 'SERVICE_NAME="zapret-gui"' in content)
    ok("NFT table patched", 'NFT_TABLE="inet zapretgui"' in content)
    ok("queue 221", "NFT_QUEUE_NUM=221" in content)
    ok("iptables chain patched", 'IPT_CHAIN="zapretgui"' in content)

    print("[conf / sites round-trip]")
    zc.ensure_conf(interface="wlan0")
    conf = zc.read_conf()
    ok("interface persisted", conf["interface"] == "wlan0")
    ok("strategy defaulted",
       conf["strategy"] == "general_alt11.bat" or conf["strategy"])
    zc.set_strategy("general.bat")
    ok("strategy changed", zc.current_strategy() == "general.bat")
    zc.write_sites(["ya.ru", "www.ya.ru"])
    ok("sites round-trip", zc.read_sites() == ["ya.ru", "www.ya.ru"])

    print("[interface detection]")
    iface = zc.detect_interface()
    ok("interface detected", bool(iface), "got: %s" % iface)

    print("[scripts syntax]")
    zc.write_host_scripts()
    for name in ("daemon.sh", "host_installer.sh", "host_uninstaller.sh"):
        p = os.path.join(td, name)
        b, err = bash_n(p)
        ok("%s bash -n" % name, b, err)

    print("[network: full download (this hits GitHub)]")
    try:
        zc.download_all(lambda m: print("   |", m))
        ok("nfqws binary exists", os.path.exists(zc.nfqws_path()))
        ver = subprocess.run([zc.nfqws_path(), "--version"],
                             capture_output=True, text=True, timeout=15)
        ok("nfqws runs --version",
           ver.returncode == 0 and "v" in (ver.stdout + ver.stderr),
           (ver.stdout or ver.stderr).strip()[:60])
        strategies = zc.read_strategies()
        ok("strategies found (%d)" % len(strategies), len(strategies) > 10,
           ", ".join(strategies[:3]))
        ok("general_alt11.bat available",
           "general_alt11.bat" in strategies)
        lists = os.listdir(os.path.join(td, "zapret-latest", "lists"))
        ok("lists expanded", "list-general-user.txt" in lists)
        ok("hardlinked user list",
           os.path.exists(zc.live_list_path()))
        ok("daemon scripts written",
           os.path.exists(zc.daemon_sh_path())
           and os.path.exists(zc.host_installer_path()))
    except Exception as e:  # noqa: BLE001
        ok("full download", False, "error: %s" % e)

    shutil.rmtree(t, ignore_errors=True)
    print("\n%d passed, %d failed" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)


def file_probe():
    return """#!/usr/bin/env bash
[[ -n "${_CONSTANTS_SH_LOADED:-}" ]] && return 0
SERVICE_NAME="zapret_discord_youtube"
FIREWALL_BACKEND="auto"
NFT_TABLE="inet zapretunix"
NFT_QUEUE_NUM=220
NFT_MARK="0x40000000"
NFT_RULE_COMMENT="Added by zapret script"
IPT_CHAIN="zapret"
IPT_CHAIN_REPLY="reply"
"""


if __name__ == "__main__":
    main()