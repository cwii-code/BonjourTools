#!/usr/bin/env python3
"""
Bonjour Browser - IE-style GUI using Apple Bonjour SDK (dns-sd.exe).
Works correctly on Windows 11 where mDNSResponder.exe owns port 5353.

Left panel : discovered mDNS/Bonjour devices (tree view)
Right panel: embedded web browser for selected device

Requirements:
    pip install tkinterweb
    Apple Bonjour SDK for Windows  (provides dns-sd.exe)
"""

import re
import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, messagebox

try:
    from tkinterweb import HtmlFrame
    HAS_WEBVIEW = True
except ImportError:
    HAS_WEBVIEW = False

# ── Service types to scan ────────────────────────────────────────────────────

SERVICE_TYPES = [
    "_http._tcp",
    "_https._tcp",
    "_ipp._tcp",
    "_ipps._tcp",
    "_printer._tcp",
    "_pdl-datastream._tcp",
    "_smb._tcp",
    "_afpovertcp._tcp",
    "_ftp._tcp",
    "_ssh._tcp",
    "_airplay._tcp",
    "_raop._tcp",
    "_googlecast._tcp",
    "_spotify-connect._tcp",
    "_daap._tcp",
    "_apple-mobdev2._tcp",
    "_device-info._tcp",
]

SERVICE_LABELS = {
    "_http._tcp":           "Web (HTTP)",
    "_https._tcp":          "Web (HTTPS)",
    "_ipp._tcp":            "Printer (IPP)",
    "_ipps._tcp":           "Printer (IPPS)",
    "_printer._tcp":        "Printer",
    "_pdl-datastream._tcp": "Printer (PDL)",
    "_smb._tcp":            "File Share (SMB)",
    "_afpovertcp._tcp":     "File Share (AFP)",
    "_ftp._tcp":            "FTP",
    "_ssh._tcp":            "SSH",
    "_airplay._tcp":        "AirPlay",
    "_raop._tcp":           "AirPlay Audio",
    "_googlecast._tcp":     "Chromecast",
    "_spotify-connect._tcp":"Spotify Connect",
    "_daap._tcp":           "iTunes Share",
    "_apple-mobdev2._tcp":  "Apple Mobile",
    "_device-info._tcp":    "Device Info",
}

# dns-sd -B output line:
#   Browsing for _http._tcp.local.
#   Timestamp     A/D Flags if Domain    Service Type         Instance Name
#   10:00:00.000  Add   3  4  local.     _http._tcp.          MyDevice
_BROWSE_RE = re.compile(
    r"^\s*[\d:\.]+\s+(Add|Rmv)\s+\S+\s+\S+\s+(\S+)\s+(\S+)\s+(.+)$"
)

# dns-sd -L output line (lookup):
#   can be "hostname.local.:port" on one line, or parsed from multiple lines
_LOOKUP_HOST_RE  = re.compile(r"hostname\s*=\s*(\S+)", re.IGNORECASE)
_LOOKUP_PORT_RE  = re.compile(r"port\s*=\s*(\d+)", re.IGNORECASE)
_LOOKUP_ADDR_RE  = re.compile(r"Address\s*=\s*([\d\.]+)", re.IGNORECASE)
# Compact form in some versions: "Name._http._tcp.local. can be reached at host.local.:80"
_LOOKUP_COMPACT_RE = re.compile(
    r"can be reached at\s+(\S+):(\d+)", re.IGNORECASE
)


def _find_dns_sd() -> str | None:
    """Find dns-sd.exe – either on PATH or in standard Bonjour install dirs."""
    exe = shutil.which("dns-sd")
    if exe:
        return exe
    candidates = [
        r"C:\Program Files\Bonjour\dns-sd.exe",
        r"C:\Program Files (x86)\Bonjour\dns-sd.exe",
        r"C:\Windows\System32\dns-sd.exe",
    ]
    for p in candidates:
        import os
        if os.path.isfile(p):
            return p
    return None


def _web_url(service_key: str, address: str, port: int) -> str | None:
    if not address:
        return None
    if "_https" in service_key or "_ipps" in service_key:
        scheme, default_port = "https", 443
    elif any(k in service_key for k in ("_http", "_ipp", "_printer", "_pdl", "_daap",
                                         "_airplay", "_googlecast")):
        scheme, default_port = "http", 80
    elif "_ftp" in service_key:
        scheme, default_port = "ftp", 21
    else:
        return None
    return f"{scheme}://{address}" if port == default_port else f"{scheme}://{address}:{port}"


def _short_name(instance: str) -> str:
    return instance.strip().rstrip(".")


# ── dns-sd wrappers ──────────────────────────────────────────────────────────

class DnsSdBrowser:
    """
    Runs 'dns-sd -B <type> local' for each service type in a background thread
    and calls on_event(action, name, stype) when a device appears/disappears.
    """

    def __init__(self, dns_sd_exe: str, service_types: list[str], on_event):
        self._exe = dns_sd_exe
        self._types = service_types
        self._on_event = on_event
        self._procs: list[subprocess.Popen] = []
        self._stop = threading.Event()

    def start(self):
        for stype in self._types:
            t = threading.Thread(target=self._browse, args=(stype,), daemon=True)
            t.start()

    def stop(self):
        self._stop.set()
        for p in self._procs:
            try:
                p.terminate()
            except Exception:
                pass

    def _browse(self, stype: str):
        cmd = [self._exe, "-B", f"{stype}.local.", "local"]
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
        except Exception:
            return
        self._procs.append(proc)

        for line in proc.stdout:
            if self._stop.is_set():
                break
            m = _BROWSE_RE.match(line)
            if m:
                action_raw, domain, stype_raw, instance = m.groups()
                action = "add" if action_raw == "Add" else "remove"
                # stype_raw looks like "_http._tcp."
                key = stype_raw.rstrip(".")
                self._on_event(action, instance.strip(), key)

        proc.stdout.close()
        proc.wait()


def lookup_device(dns_sd_exe: str, instance: str, stype: str,
                  timeout: float = 3.0) -> dict:
    """
    Run 'dns-sd -L <instance> <stype> local' and parse host/port/address.
    Returns dict with keys: server, port, address.
    """
    cmd = [dns_sd_exe, "-L", instance, f"{stype}.", "local"]
    result = {"server": "", "port": 80, "address": ""}
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        ).stdout
    except Exception:
        return result

    compact = _LOOKUP_COMPACT_RE.search(out)
    if compact:
        result["server"] = compact.group(1).rstrip(".")
        result["port"]   = int(compact.group(2))
    else:
        mh = _LOOKUP_HOST_RE.search(out)
        mp = _LOOKUP_PORT_RE.search(out)
        if mh:
            result["server"] = mh.group(1).rstrip(".")
        if mp:
            result["port"] = int(mp.group(1))

    # Resolve address
    ma = _LOOKUP_ADDR_RE.search(out)
    if ma:
        result["address"] = ma.group(1)
    elif result["server"]:
        try:
            import socket
            result["address"] = socket.gethostbyname(result["server"])
        except Exception:
            pass

    return result


# ── Main application ─────────────────────────────────────────────────────────

class BonjourBrowser(tk.Tk):
    _WELCOME_HTML = """
    <html><body style="font-family:Segoe UI,Arial,sans-serif;
                        display:flex;align-items:center;
                        justify-content:center;height:90vh;margin:0;
                        background:#f8f8f8;color:#555;">
      <div style="text-align:center">
        <h2 style="color:#0078d7">Bonjour Browser</h2>
        <p>Click <b>Scan</b> to discover devices on your network.<br>
           Select a device on the left to open its web page here.</p>
        <p style="font-size:12px;color:#aaa">
          Uses Apple Bonjour SDK (dns-sd.exe) — works on Windows 11
        </p>
      </div>
    </body></html>"""

    def __init__(self):
        super().__init__()
        self.title("Bonjour Browser")
        self.geometry("1200x700")
        self.minsize(800, 500)

        self._dns_sd = _find_dns_sd()
        self._browser: DnsSdBrowser | None = None
        self._scanning = False
        self._devices: dict[str, dict] = {}     # "instance|stype" -> info
        self._cat_nodes: dict[str, str] = {}    # label -> tree iid
        self._dev_nodes: dict[str, str] = {}    # key   -> tree iid

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        if not self._dns_sd:
            messagebox.showwarning(
                "dns-sd not found",
                "dns-sd.exe was not found.\n\n"
                "Please install Apple Bonjour SDK for Windows and make sure\n"
                "dns-sd.exe is in your PATH or in C:\\Program Files\\Bonjour\\",
            )

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self):
        self._build_toolbar()
        self._build_main_pane()
        self._build_statusbar()

    def _build_toolbar(self):
        bar = ttk.Frame(self)
        bar.pack(fill=tk.X, padx=6, pady=(6, 2))

        self.btn_scan = ttk.Button(bar, text="▶  Scan",  width=10, command=self._start_scan)
        self.btn_stop = ttk.Button(bar, text="■  Stop",  width=10, command=self._stop_scan,
                                   state=tk.DISABLED)
        self.btn_scan.pack(side=tk.LEFT, padx=(0, 4))
        self.btn_stop.pack(side=tk.LEFT, padx=(0, 10))

        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, pady=2, padx=4)

        self._status = tk.StringVar(value="Ready — click Scan to start.")
        ttk.Label(bar, textvariable=self._status, foreground="#444").pack(side=tk.LEFT, padx=6)

        # dns-sd path indicator
        dns_sd_text = f"dns-sd: {self._dns_sd}" if self._dns_sd else "dns-sd: NOT FOUND"
        dns_sd_color = "#080" if self._dns_sd else "#c00"
        ttk.Label(bar, text=dns_sd_text, foreground=dns_sd_color,
                  font=("Segoe UI", 8)).pack(side=tk.RIGHT, padx=6)

    def _build_main_pane(self):
        pane = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        pane.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)

        # ── Left ──────────────────────────────────────────────────────────
        left = ttk.Frame(pane, width=290)
        left.pack_propagate(False)
        pane.add(left, weight=1)

        ttk.Label(left, text="Local Network Devices",
                  font=("Segoe UI", 9, "bold"), foreground="#333").pack(
                  anchor=tk.W, padx=6, pady=(2, 2))

        wrap = ttk.Frame(left)
        wrap.pack(fill=tk.BOTH, expand=True)

        self._tree = ttk.Treeview(wrap, selectmode="browse", show="tree")
        vsb = ttk.Scrollbar(wrap, orient=tk.VERTICAL, command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._tree.pack(fill=tk.BOTH, expand=True)
        self._tree.tag_configure("cat", font=("Segoe UI", 9, "bold"), foreground="#0078d7")
        self._tree.tag_configure("dev", font=("Segoe UI", 9))
        self._tree.bind("<<TreeviewSelect>>", self._on_select)

        # ── Right ─────────────────────────────────────────────────────────
        right = ttk.Frame(pane)
        pane.add(right, weight=5)

        url_bar = ttk.Frame(right)
        url_bar.pack(fill=tk.X, padx=4, pady=(2, 2))

        ttk.Button(url_bar, text="◀", width=3, command=self._go_back).pack(side=tk.LEFT)
        ttk.Button(url_bar, text="▶", width=3, command=self._go_forward).pack(side=tk.LEFT, padx=2)
        ttk.Button(url_bar, text="↺", width=3, command=self._go_reload).pack(side=tk.LEFT, padx=(2, 8))

        self._url_var = tk.StringVar()
        url_entry = ttk.Entry(url_bar, textvariable=self._url_var, font=("Segoe UI", 9))
        url_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        url_entry.bind("<Return>", lambda _e: self._load_url(self._url_var.get()))

        ttk.Button(url_bar, text="Go", width=4,
                   command=lambda: self._load_url(self._url_var.get())).pack(side=tk.LEFT, padx=(4, 0))

        if HAS_WEBVIEW:
            self._webview = HtmlFrame(right, messages_enabled=False)
            self._webview.pack(fill=tk.BOTH, expand=True)
            self._webview.load_html(self._WELCOME_HTML)
        else:
            self._webview = None
            f = ttk.Frame(right, relief=tk.SUNKEN)
            f.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
            ttk.Label(
                f,
                text=(
                    "tkinterweb is not installed.\n\n"
                    "    pip install tkinterweb\n\n"
                    "Without it, clicking a device opens your default browser."
                ),
                justify=tk.CENTER, foreground="#666", font=("Segoe UI", 10),
            ).pack(expand=True)

    def _build_statusbar(self):
        bar = ttk.Frame(self, relief=tk.SUNKEN)
        bar.pack(fill=tk.X, side=tk.BOTTOM)
        self._count_var = tk.StringVar(value="No devices found.")
        ttk.Label(bar, textvariable=self._count_var, anchor=tk.W,
                  foreground="#555").pack(side=tk.LEFT, padx=6, pady=1)

    # ── Scan control ──────────────────────────────────────────────────────

    def _start_scan(self):
        if self._scanning or not self._dns_sd:
            return
        self._scanning = True
        self.btn_scan.configure(state=tk.DISABLED)
        self.btn_stop.configure(state=tk.NORMAL)
        self._status.set("Scanning via Bonjour SDK…")

        for iid in self._tree.get_children():
            self._tree.delete(iid)
        self._devices.clear()
        self._cat_nodes.clear()
        self._dev_nodes.clear()
        self._count_var.set("Scanning…")

        self._browser = DnsSdBrowser(self._dns_sd, SERVICE_TYPES, self._on_device_event)
        self._browser.start()

    def _stop_scan(self):
        self._scanning = False
        if self._browser:
            threading.Thread(target=self._browser.stop, daemon=True).start()
            self._browser = None
        self.btn_scan.configure(state=tk.NORMAL)
        self.btn_stop.configure(state=tk.DISABLED)
        n = len(self._devices)
        self._status.set(f"Stopped.  {n} device(s) found.")
        self._count_var.set(f"{n} device(s) found.")

    # ── Device events ─────────────────────────────────────────────────────

    def _on_device_event(self, action: str, instance: str, stype: str):
        self.after(0, self._apply_event, action, instance, stype)

    def _apply_event(self, action: str, instance: str, stype: str):
        key = f"{instance}|{stype}"
        if action == "remove":
            iid = self._dev_nodes.pop(key, None)
            if iid:
                self._tree.delete(iid)
            self._devices.pop(key, None)
        else:
            if key not in self._devices:
                self._devices[key] = {
                    "instance": instance,
                    "stype": stype,
                    "server": "",
                    "port": 80,
                    "address": "",
                }
            label = SERVICE_LABELS.get(stype, stype)

            if label not in self._cat_nodes:
                cat = self._tree.insert("", tk.END, text=f"  {label}",
                                        open=True, tags=("cat",))
                self._cat_nodes[label] = cat

            cat = self._cat_nodes[label]
            short = _short_name(instance)

            if key not in self._dev_nodes:
                iid = self._tree.insert(cat, tk.END,
                                        text=f"  {short}",
                                        tags=("dev",),
                                        values=(key,))
                self._dev_nodes[key] = iid

        n = len(self._devices)
        self._count_var.set(f"{n} device(s) found.")
        if self._scanning:
            self._status.set(f"Scanning…  {n} device(s) found so far.")

    # ── Navigation ────────────────────────────────────────────────────────

    def _on_select(self, _event=None):
        sel = self._tree.selection()
        if not sel:
            return
        vals = self._tree.item(sel[0], "values")
        if not vals:
            return
        key = vals[0]
        device = self._devices.get(key)
        if not device:
            return

        self._status.set(f"Resolving {device['instance']}…")

        def resolve():
            info = lookup_device(self._dns_sd, device["instance"], device["stype"])
            device.update(info)
            self.after(0, self._navigate_to, device)

        threading.Thread(target=resolve, daemon=True).start()

    def _navigate_to(self, device: dict):
        url = _web_url(device["stype"], device["address"], device["port"])
        self._status.set(f"Ready.")
        if url:
            self._load_url(url)
        else:
            self._show_info_page(device)

    def _load_url(self, url: str):
        if not url.strip():
            return
        self._url_var.set(url)
        if self._webview:
            self._webview.load_url(url)
        else:
            import webbrowser
            webbrowser.open(url)

    def _go_back(self):
        if self._webview:
            try:
                self._webview.go_back()
            except Exception:
                pass

    def _go_forward(self):
        if self._webview:
            try:
                self._webview.go_forward()
            except Exception:
                pass

    def _go_reload(self):
        url = self._url_var.get()
        if url:
            self._load_url(url)

    def _show_info_page(self, device: dict):
        label = SERVICE_LABELS.get(device["stype"], device["stype"])
        short = _short_name(device["instance"])
        html  = f"""
        <html><body style="font-family:Segoe UI,Arial,sans-serif;
                           padding:32px;color:#333;background:#fff">
          <h2 style="color:#0078d7;margin-bottom:4px">{short}</h2>
          <p style="color:#888;margin-top:0">{label}</p>
          <hr style="border:none;border-top:1px solid #eee;margin:16px 0">
          <table style="border-collapse:collapse;font-size:14px">
            <tr><td style="color:#888;padding:4px 24px 4px 0">Host</td>
                <td><b>{device.get('server','N/A')}</b></td></tr>
            <tr><td style="color:#888;padding:4px 24px 4px 0">IP Address</td>
                <td>{device.get('address','N/A')}</td></tr>
            <tr><td style="color:#888;padding:4px 24px 4px 0">Port</td>
                <td>{device.get('port','N/A')}</td></tr>
            <tr><td style="color:#888;padding:4px 24px 4px 0">Service</td>
                <td>{device['stype']}</td></tr>
          </table>
          <p style="color:#aaa;margin-top:32px;font-size:13px">
            This service type does not have a web interface.
          </p>
        </body></html>"""
        self._url_var.set(f"bonjour://{device.get('server', device['instance'])}")
        if self._webview:
            self._webview.load_html(html)

    # ── Close ─────────────────────────────────────────────────────────────

    def _on_close(self):
        self._stop_scan()
        self.after(400, self.destroy)


if __name__ == "__main__":
    app = BonjourBrowser()
    app.mainloop()
