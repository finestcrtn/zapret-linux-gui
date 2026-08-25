#!/usr/bin/env python3
"""Zapret GUI -- Flatpak edition.

Controls a host-side zapret daemon (own systemd unit "zapret-gui") through
flatpak-spawn --host. Downloads all components from GitHub into the app's own
data dir. Never touches an existing system zapret install.

First launch runs an automatic setup phase ("Downloading zapret…" →
"Installing service…" → "Starting zapret…"); afterwards the app shows the
regular layout with no setup controls.
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
import urllib.request
from urllib.parse import urlparse, urljoin

import zapretctl as zc

# ---- Dark theme palette ------------------------------------------------------
BG = "#16161d"
BG_ALT = "#1f1f2a"
FG = "#e8e8f0"
FG_DIM = "#8a8a9a"
ACCENT = "#7aa2f7"
ACCENT_ACTIVE = "#5d87e8"
GREEN = "#9ece6a"
RED = "#f7768e"
AMBER = "#e0af68"
GRID = "#2a2a38"


def run_svc(cmd, timeout=60):
    """systemctl against OUR unit, executed on the host."""
    return zc.run_host(["systemctl", cmd, zc.SERVICE_UNIT], timeout=timeout,
                       check=False)


def service_state():
    return zc.service_state()


def service_installed():
    for _ in range(2):
        rc, out, _ = zc.run_host(["systemctl", "show", "-p", "LoadState",
                                  "--value", zc.SERVICE_UNIT], timeout=15,
                                 check=False)
        if rc == 0 and out.strip() == "loaded":
            return True
        time.sleep(0.4)
    return False


# ---- site scan helpers (same as original) ------------------------------------
def normalize_url(raw):
    raw = raw.strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "https://" + raw
    try:
        host = urlparse(raw).hostname
    except ValueError:
        return None
    if not host:
        return None
    host = re.sub(r"^www\.", "", host.lower().strip("."))
    return host or None


def fetch_page_html(host, timeout=15):
    for scheme in ("https", "http"):
        url = f"{scheme}://{host}/"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
                enc = r.headers.get_content_charset() or "utf-8"
                try:
                    return data.decode(enc, errors="replace")
                except LookupError:
                    return data.decode("utf-8", errors="replace")
        except Exception:
            continue
    return None


def extract_domains(html, base_host):
    hosts = set()
    url_re = re.compile(
        r"""(?:src|href|data-src|poster|action)\s*=\s*["']([^"']+)["']"""
        r"""|url\(\s*["']?([^"')]+)["']?\s*\)""",
        re.IGNORECASE,
    )
    for m in url_re.finditer(html):
        raw = m.group(1) or m.group(2)
        raw = raw.strip()
        if not raw or raw.startswith(("#", "javascript:", "data:", "mailto:", "tel:")):
            continue
        joined = urljoin(f"https://{base_host}/", raw)
        try:
            host = urlparse(joined).hostname
        except ValueError:
            continue
        if not host:
            continue
        host = re.sub(r"^www\.", "", host.lower().strip("."))
        if host and host != base_host and "." in host:
            hosts.add(host)
    return sorted(hosts)


def is_reachable(host, timeout=6):
    for scheme in ("https", "http"):
        req = urllib.request.Request(f"{scheme}://{host}/", method="GET",
                                     headers={"User-Agent": "Mozilla/5.0",
                                              "Accept": "*/*"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status < 500
        except Exception:
            continue
    return False


def scan_site(host, timeout=10):
    html = fetch_page_html(host, timeout=timeout)
    if html is None:
        return None
    domains = extract_domains(html, host)
    results = []
    for d in domains:
        results.append((d, is_reachable(d, timeout=max(4, timeout // 2))))
    results.sort(key=lambda x: (x[1], x[0]))
    return results


# -----------------------------------------------------------------------------
class ZapretGui:
    def __init__(self, root):
        self.root = root
        root.title("Zapret Control")
        root.configure(bg=BG)
        root.resizable(False, False)

        self.active = tk.BooleanVar(value=False)
        self.busy = False
        self.mode = "setup"
        self.retry_visible = False

        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=FG, fieldbackground=BG_ALT,
                        bordercolor=GRID, lightcolor=BG, darkcolor=BG,
                        troughcolor=BG_ALT, selectbackground=ACCENT,
                        selectforeground=BG, focuscolor=ACCENT)
        style.configure("TFrame", background=BG)
        style.configure("TLabelframe", background=BG, foreground=FG, bordercolor=GRID)
        style.configure("TLabelframe.Label", background=BG, foreground=FG)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Dim.TLabel", background=BG, foreground=FG_DIM)
        style.configure("TButton", background=BG_ALT, foreground=FG,
                        bordercolor=GRID, padding=(10, 6), focuscolor=ACCENT)
        style.map("TButton", background=[("active", GRID), ("disabled", BG)],
                  foreground=[("disabled", FG_DIM)])
        style.configure("Accent.TButton", background=ACCENT, foreground=BG,
                        padding=(10, 6), focuscolor=ACCENT)
        style.map("Accent.TButton", background=[("active", ACCENT_ACTIVE),
                                                ("disabled", BG)],
                  foreground=[("disabled", FG_DIM)])
        style.configure("TCombobox", fieldbackground=BG_ALT, background=BG_ALT,
                        foreground=FG, arrowcolor=FG, bordercolor=GRID)
        style.map("TCombobox", fieldbackground=[("readonly", BG_ALT)])
        root.option_add("*TCombobox*Listbox.background", BG_ALT)
        root.option_add("*TCombobox*Listbox.foreground", FG)
        root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        root.option_add("*TCombobox*Listbox.selectForeground", BG)

        self.outer = tk.Frame(root, bg=BG)
        self.outer.pack(fill="both", expand=True, padx=18, pady=16)

        self._build_normal()
        self._build_setup()

        # start in setup mode; switched to normal once readiness is known
        self._show_mode("setup")
        self.root.update_idletasks()
        self.root.geometry(f"{self.root.winfo_reqwidth()}x{self.root.winfo_reqheight()}")

        self.poll_status()
        self.after_startup_check()

    # ---- UI construction ----------------------------------------------------
    def _build_normal(self):
        head = tk.Frame(self.outer, bg=BG)
        self.indicator = tk.Label(head, text="●", font=("DejaVu Sans", 30), bg=BG)
        self.indicator.pack(side="left", padx=(0, 12))
        self.status_text = tk.Label(head, text="Checking...", font=("Sans", 13, "bold"),
                                    bg=BG, fg=FG)
        self.status_text.pack(side="left")
        self.strat_text = tk.Label(head, text="", font=("Sans", 9), bg=BG, fg=FG_DIM)
        self.strat_text.pack(side="right")
        self.head = head

        self.btn = tk.Button(self.outer, text="Start", font=("Sans", 12, "bold"),
                             bg=ACCENT, fg=BG, activebackground=ACCENT_ACTIVE,
                             activeforeground=BG, relief="flat", cursor="hand2",
                             borderwidth=0, padx=12, pady=10, command=self.toggle)

        # --- Add site section ---
        site_box = tk.Frame(self.outer, bg=BG_ALT, highlightbackground=GRID,
                            highlightthickness=1)
        tk.Label(site_box, text="Unblock a site", font=("Sans", 10, "bold"),
                 bg=BG_ALT, fg=FG).pack(anchor="w", padx=12, pady=(10, 2))
        tk.Label(site_box, text="Paste a URL, e.g. https://ya.ru/ or ya.ru",
                 font=("Sans", 8), bg=BG_ALT, fg=FG_DIM).pack(anchor="w", padx=12)
        entry_row = tk.Frame(site_box, bg=BG_ALT)
        entry_row.pack(fill="x", padx=12, pady=(8, 12))
        self.url_var = tk.StringVar()
        self.url_entry = tk.Entry(entry_row, textvariable=self.url_var, bg=BG,
                                  fg=FG, insertbackground=FG, relief="flat",
                                  highlightbackground=GRID, highlightthickness=1,
                                  font=("Sans", 10))
        self.url_entry.pack(side="left", fill="x", expand=True, ipady=6)
        self.url_entry.bind("<Return>", lambda e: self.add_site())
        self.add_btn = tk.Button(entry_row, text="Add", font=("Sans", 10, "bold"),
                                 bg=ACCENT, fg=BG, activebackground=ACCENT_ACTIVE,
                                 activeforeground=BG, relief="flat", cursor="hand2",
                                 borderwidth=0, padx=16, command=self.add_site)
        self.add_btn.pack(side="left", padx=(8, 0))
        sites_row = tk.Frame(site_box, bg=BG_ALT)
        sites_row.pack(fill="x", padx=12, pady=(0, 12))
        tk.Label(sites_row, text="Added:", font=("Sans", 8), bg=BG_ALT, fg=FG_DIM).pack(side="left")
        self.sites_label = tk.Label(sites_row, text="(none)", font=("Sans", 8),
                                    bg=BG_ALT, fg=FG, anchor="w", justify="left",
                                    wraplength=290)
        self.sites_label.pack(side="left", padx=(6, 0), fill="x", expand=True)
        self.rm_btn = tk.Button(sites_row, text="✕", font=("Sans", 9),
                                bg=BG, fg=FG_DIM, relief="flat", cursor="hand2",
                                activebackground=RED, activeforeground=BG,
                                command=self.remove_last_site)
        self.rm_btn.pack(side="right", anchor="n")
        self.site_box = site_box

        # --- Strategy picker ---
        strat_box = tk.Frame(self.outer, bg=BG_ALT, highlightbackground=GRID,
                             highlightthickness=1)
        tk.Label(strat_box, text="Strategy", font=("Sans", 10, "bold"),
                 bg=BG_ALT, fg=FG).pack(anchor="w", padx=12, pady=(10, 2))
        tk.Label(strat_box, text="If a site loads badly, try another strategy and Apply",
                 font=("Sans", 8), bg=BG_ALT, fg=FG_DIM).pack(anchor="w", padx=12)
        strat_row = tk.Frame(strat_box, bg=BG_ALT)
        strat_row.pack(fill="x", padx=12, pady=(8, 12))
        strategies = zc.read_strategies()
        self.strat_var = tk.StringVar()
        self.strat_combo = ttk.Combobox(strat_row, textvariable=self.strat_var,
                                        values=strategies, state="readonly",
                                        font=("Sans", 10))
        self.strat_combo.pack(side="left", fill="x", expand=True, ipady=4)
        self.apply_btn = tk.Button(strat_row, text="Apply", font=("Sans", 10, "bold"),
                                   bg=ACCENT, fg=BG, activebackground=ACCENT_ACTIVE,
                                   activeforeground=BG, relief="flat", cursor="hand2",
                                   borderwidth=0, padx=16, command=self.apply_strategy)
        self.apply_btn.pack(side="left", padx=(8, 0))
        self.strat_box = strat_box

        # --- Update + version ---
        upd_row = tk.Frame(self.outer, bg=BG)
        self.update_btn = tk.Button(upd_row, text="Check for updates",
                                    font=("Sans", 10, "bold"), bg=BG_ALT, fg=FG,
                                    activebackground=GRID, relief="flat",
                                    cursor="hand2", borderwidth=0, padx=12,
                                    pady=8, command=self.check_update)
        self.update_btn.pack(side="left")
        self.version_text = tk.Label(upd_row, text="", font=("Sans", 8), bg=BG, fg=FG_DIM)
        self.version_text.pack(side="right")
        self.upd_row = upd_row

        # --- Log ---
        self.log = tk.Text(self.outer, height=6, bg=BG, fg=FG_DIM, relief="flat",
                           highlightbackground=GRID, highlightthickness=1,
                           font=("Monospace", 8), padx=8, pady=6, state="disabled",
                           insertbackground=FG)

        cur = zc.current_strategy()
        if cur:
            self.strat_var.set(cur)
        else:
            self.strat_var.set(strategies[0] if strategies else "")
        self._refresh_sites()

    def _build_setup(self):
        sf = tk.Frame(self.outer, bg=BG_ALT, highlightbackground=GRID,
                      highlightthickness=1)
        self.setup_status = tk.Label(sf, text="", font=("Sans", 12, "bold"),
                                     bg=BG_ALT, fg=FG, anchor="center",
                                     justify="center", wraplength=380)
        self.setup_status.pack(fill="x", padx=24, pady=(28, 6))
        self.setup_sub = tk.Label(sf, text="", font=("Sans", 9), bg=BG_ALT,
                                  fg=FG_DIM, anchor="center", justify="center",
                                  wraplength=380)
        self.setup_sub.pack(fill="x", padx=24, pady=(0, 8))
        self.retry_btn = tk.Button(sf, text="Retry", font=("Sans", 10, "bold"),
                                   bg=ACCENT, fg=BG, activebackground=ACCENT_ACTIVE,
                                   activeforeground=BG, relief="flat",
                                   cursor="hand2", borderwidth=0, padx=18,
                                   command=self._retry_setup)
        self.retry_btn.pack(pady=(4, 22))
        self.setup_frame = sf

    # ---- layout switching ----------------------------------------------------
    def _show_mode(self, mode):
        self.mode = mode
        for w in (self.head, self.btn, self.site_box, self.strat_box,
                  self.upd_row, self.log, self.setup_frame):
            w.pack_forget()
        if mode == "normal":
            self.head.pack(fill="x")
            self.btn.pack(fill="x", pady=(0, 12))
            self.site_box.pack(fill="x", pady=(0, 10))
            self.strat_box.pack(fill="x", pady=(0, 10))
            self.upd_row.pack(fill="x", pady=(2, 10))
            self.log.pack(fill="both", expand=True)
        else:
            self.setup_frame.pack(fill="both", expand=True, pady=(8, 8))
        self.root.update_idletasks()
        self.root.geometry(f"{self.root.winfo_reqwidth()}x{self.root.winfo_reqheight()}")

    # ---- setup phase ---------------------------------------------------------
    def after_startup_check(self):
        threading.Thread(target=self._startup_worker, daemon=True).start()

    def _startup_worker(self):
        ready = zc.is_ready()
        inst = service_installed()
        self.root.after(0, lambda: self._startup_decide(ready, inst))

    def _startup_decide(self, ready, inst):
        if ready and inst:
            self._show_mode("normal")
            self.log_line("Service " + zc.SERVICE_UNIT + " ready.")
            self.poll_status()
        else:
            self._show_mode("setup")
            self.log_line("Setup started (components present: %s, service: %s)"
                          % (ready, inst))
            threading.Thread(target=self._setup_pipeline, args=(ready, inst),
                             daemon=True).start()

    def _set_setup(self, title, sub="", title_fg=FG):
        self._set_retry(False)
        self.setup_status.config(text=title, fg=title_fg)
        self.setup_sub.config(text=sub, fg=FG_DIM)

    def _setup_progress(self, msg):
        self.root.after(0, lambda: self.setup_sub.config(text=msg))

    def _setup_pipeline(self, ready, inst):
        try:
            if not ready:
                self._set_setup("Downloading zapret…",
                                "fetching components from GitHub")
                self.log_line("Downloading zapret components…")
                zc.download_all(self._setup_progress)
                ready = True

            # Service may already be installed (e.g. a previous launch, or the
            # first check raced). In that case never auto-start -- go to the
            # normal view and let the user press Start.
            if service_installed():
                self.root.after(0, self._setup_done)
                return

            self._set_setup("Installing service…",
                            "a password prompt may appear.\n"
                            "Enter your password to continue.")
            self.log_line("Installing service (admin prompt)…")
            zc.write_host_scripts()
            rc, out, err = zc.run_host(
                ["pkexec", "/usr/bin/bash", zc.host_installer_path()],
                timeout=180, check=False)
            time.sleep(1)
            if not service_installed():
                raise RuntimeError(
                    "The service was not installed." + ((" " + err) if err else ""))

            self._set_setup("Starting zapret…")
            self.log_line("Starting zapret…")
            run_svc("start", timeout=90)
            time.sleep(2)
            self.root.after(0, self._setup_done)
        except Exception as e:  # noqa: BLE001
            self.log_line("Setup error: %s" % e)
            self.root.after(0, lambda: self._setup_error(str(e)))

    def _setup_done(self):
        self._set_setup("", "")
        self._show_mode("normal")
        self._refresh_sites()
        self.log_line("Setup finished.")
        self.poll_status()

    def _setup_error(self, msg):
        self._set_setup("Setup failed", msg + "\n\nYou can retry, "
                        "or close this window.", title_fg=RED)
        self._set_retry(True)

    def _set_retry(self, on):
        if on and not self.retry_visible:
            self.retry_btn.pack(pady=(4, 22))
            self.retry_visible = True
        elif not on and self.retry_visible:
            self.retry_btn.pack_forget()
            self.retry_visible = False
            self.root.update_idletasks()
            self.root.geometry(f"{self.root.winfo_reqwidth()}x"
                               f"{self.root.winfo_reqheight()}")

    def _retry_setup(self):
        self._set_setup("Preparing…", "checking components")
        threading.Thread(target=self._startup_worker, daemon=True).start()

    # ---- helpers -------------------------------------------------------------
    def log_line(self, msg):
        self.log.config(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def set_busy(self, busy, text=None):
        self.busy = busy
        for w in (self.btn, self.add_btn, self.apply_btn, self.update_btn,
                  self.rm_btn):
            w.config(state="disabled" if busy else "normal")
        if busy and text:
            self.status_text.config(text=text)
            self.indicator.config(text="…", fg=AMBER)

    # ---- status --------------------------------------------------------------
    def poll_status(self):
        st = service_state()
        self.active.set(st == "active")
        if st == "active":
            self.indicator.config(text="●", fg=GREEN)
            self.status_text.config(text="Active — DPI bypass enabled")
            self.btn.config(text="Stop", bg=RED, fg=BG, activebackground="#e0607a",
                            activeforeground=BG)
        else:
            self.indicator.config(text="●", fg="#55555f")
            self.status_text.config(text="Inactive — bypass disabled")
            self.btn.config(text="Start", bg=ACCENT, fg=BG, activebackground=ACCENT_ACTIVE,
                            activeforeground=BG)
        cur = zc.current_strategy()
        if cur:
            self.strat_text.config(text=cur)
        self.root.after(2000, self.poll_status)

    def toggle(self):
        if self.busy:
            return
        if not service_installed():
            self.log_line("Service not installed; run setup by restarting the app.")
            return
        target = "stop" if self.active.get() else "start"
        self.set_busy(True)
        self.log_line(f"{'Stopping' if target == 'stop' else 'Starting'} zapret...")
        threading.Thread(target=self._do_toggle, args=(target,), daemon=True).start()

    def _do_toggle(self, target):
        rc, _, err = run_svc(target, timeout=90)
        time.sleep(2)
        st = service_state()
        self.root.after(0, lambda: self._done(rc, err, st))

    # ---- add site ------------------------------------------------------------
    def add_site(self):
        if self.busy:
            return
        host = normalize_url(self.url_var.get())
        if not host:
            messagebox.showwarning("Zapret GUI", "Enter a valid URL, e.g. https://ya.ru/ or ya.ru")
            return
        variants = {host}
        if host.startswith("www."):
            variants.add(host[4:])
        else:
            variants.add("www." + host)
        sites = zc.read_sites()
        new = [v for v in sorted(variants) if v not in sites]
        if not new:
            self.log_line(f"{host} already in list")
            self.url_var.set("")
            return
        sites.extend(new)
        zc.write_sites(sites)
        zc.apply_sites_to_list(sites)
        self.url_var.set("")
        self._refresh_sites()
        self.set_busy(True)
        self.log_line(f"Added {host}. Restarting service...")
        threading.Thread(target=self._restart_svc_and_scan, args=(host,), daemon=True).start()

    def _restart_svc_and_scan(self, host):
        rc, _, err = run_svc("restart", timeout=90)
        time.sleep(2)
        self.root.after(0, lambda: self.log_line(err or ""))
        self.root.after(0, lambda: self._done_svc())
        try:
            results = None
            for _ in range(3):
                results = scan_site(host, timeout=10)
                if results is not None:
                    break
                time.sleep(3)
            if results is None:
                self.log_line("Couldn't read %s's page; no linked domains captured." % host)
                return
            blocked = [d for d, ok in results if not ok]
            if not blocked:
                self.log_line(f"All linked domains for {host} are reachable — nothing to add")
                return
            candidates = set()
            for d in blocked:
                candidates.add(d)
                candidates.add("www." + d if not d.startswith("www.") else d[4:])
            sites = zc.read_sites()
            added = [d for d in sorted(candidates) if d not in sites]
            if not added:
                self.log_line("Blocked linked domains already in the list")
                return
            sites.extend(added)
            zc.write_sites(sites)
            zc.apply_sites_to_list(sites)
            self.log_line(f"Added blocked resource domain(s): {', '.join(added)}")
            self.root.after(0, self._refresh_sites)
            threading.Thread(target=self._restart_svc, daemon=True).start()
        except Exception as e:  # noqa: BLE001
            self.log_line("scan error: %s" % e)

    def _restart_svc(self):
        run_svc("restart", timeout=90)
        time.sleep(1)
        self.root.after(0, lambda: self._done_svc())

    def _done_svc(self):
        self.set_busy(False)
        self._refresh_sites()
        self.poll_status()

    def remove_last_site(self):
        if self.busy:
            return
        sites = zc.read_sites()
        if not sites:
            return
        removed = sites.pop()
        zc.write_sites(sites)
        zc.apply_sites_to_list(sites)
        self._refresh_sites()
        self.set_busy(True)
        self.log_line(f"Removed {removed}. Restarting service...")
        threading.Thread(target=self._restart_svc, daemon=True).start()

    def _done(self, rc, err, st):
        self.set_busy(False)
        self.active.set(st == "active")
        if rc == 0:
            self.log_line("zapret is " + ("active" if st == "active" else "inactive"))
        else:
            self.log_line("Failed: " + (err or "unknown error"))
        self.poll_status()

    def _refresh_sites(self):
        sites = zc.read_sites()
        if sites:
            self.sites_label.config(text=", ".join(sites))
            self.rm_btn.config(state="normal")
        else:
            self.sites_label.config(text="(none)")
            self.rm_btn.config(state="disabled")

    # ---- strategy ------------------------------------------------------------
    def apply_strategy(self):
        if self.busy:
            return
        name = self.strat_var.get()
        if not name:
            return
        cur = zc.current_strategy()
        if cur == name:
            self.log_line(f"Strategy already {name}")
            return
        if not zc.set_strategy(name):
            self.log_line("Could not write config")
            return
        self.set_busy(True)
        self.log_line(f"Switching strategy to {name}. Restarting...")
        threading.Thread(target=self._restart_svc, daemon=True).start()

    # ---- update --------------------------------------------------------------
    def check_update(self):
        if self.busy:
            return
        self.set_busy(True)
        self.log_line("Checking for updates...")
        threading.Thread(target=self._check_worker, daemon=True).start()

    def _check_worker(self):
        try:
            latest = zc.latest_zapret_tag()
            installed = self._installed_version()
            self.root.after(0, lambda: self._check_done(latest, installed))
        except Exception as e:  # noqa: BLE001
            self.log_line("Update check failed: %s" % e)
            self.root.after(0, lambda: self.set_busy(False))

    def _check_done(self, latest, installed):
        self.set_busy(False)
        if latest != installed:
            self.version_text.config(text=f"nfqws {installed} → {latest}",
                                     foreground=AMBER)
            self.update_btn.config(text="Download update", command=self.do_update)
            self.log_line(f"Update available: nfqws {latest}")
        else:
            self.version_text.config(text=f"nfqws {latest} · up to date",
                                     foreground=GREEN)
            self.update_btn.config(text="Check for updates", command=self.check_update)
            self.log_line("Already up to date")

    def _installed_version(self):
        vf = os.path.join(zc.tool_dir(), "version.json")
        if os.path.exists(vf):
            try:
                return json.load(open(vf)).get("nfqws", zc.ZAPRET_VERSION)
            except Exception:  # noqa: BLE001
                pass
        return zc.ZAPRET_VERSION

    def do_update(self):
        if self.busy:
            return
        self.log_line("Updating components, please wait...")
        self.set_busy(True)
        threading.Thread(target=self._update_worker, daemon=True).start()

    def _update_worker(self):
        try:
            zc.download_all(self._cb_progress)
            zc.ensure_conf()
            self._save_installed_lists()
            json.dump({"nfqws": zc.latest_zapret_tag()},
                      open(os.path.join(zc.tool_dir(), "version.json"), "w"))
            run_svc("restart", timeout=90)
            self.root.after(0, self._update_done)
        except Exception as e:  # noqa: BLE001
            self.log_line("Update error: %s" % e)
            self.root.after(0, lambda: self.set_busy(False))

    def _save_installed_lists(self):
        st = zc.read_sites()
        if st:
            zc.write_sites(st)
            zc.apply_sites_to_list(st)

    def _cb_progress(self, msg):
        self.root.after(0, lambda: self.log_line(msg))

    def _update_done(self):
        self.set_busy(False)
        self.log_line("Update complete.")
        self._refresh_sites()
        self.check_update()


def main():
    root = tk.Tk()
    ZapretGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()