#!/usr/bin/env python3
"""
Bonjour Browser - discovers mDNS/Bonjour devices on the local network.
Double-click a device to open its web page in the default browser.

Requirements:
    pip install zeroconf
"""

import socket
import threading
import tkinter as tk
from tkinter import ttk
from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

# ── Service types to scan ────────────────────────────────────────────────────

SERVICE_TYPES = [
    "_http._tcp.local.",
    "_https._tcp.local.",
    "_http-alt._tcp.local.",
    "_rfb._tcp.local.",
    "_rdlink._tcp.local.",
    "_teamviewer._tcp.local.",
    "_ipp._tcp.local.",
    "_ipps._tcp.local.",
    "_printer._tcp.local.",
    "_pdl-datastream._tcp.local.",
    "_scanner._tcp.local.",
    "_uscan._tcp.local.",
    "_smb._tcp.local.",
    "_afpovertcp._tcp.local.",
    "_ftp._tcp.local.",
    "_ssh._tcp.local.",
    "_airplay._tcp.local.",
    "_raop._tcp.local.",
    "_companion-link._tcp.local.",
    "_googlecast._tcp.local.",
    "_spotify-connect._tcp.local.",
    "_daap._tcp.local.",
    "_apple-mobdev2._tcp.local.",
    "_device-info._tcp.local.",
]

SERVICE_LABELS = {
    "_http._tcp.local.":            "Web (HTTP)",
    "_https._tcp.local.":           "Web (HTTPS)",
    "_http-alt._tcp.local.":        "Web (HTTP alt port)",
    "_rfb._tcp.local.":             "KVM / VNC (RFB)",
    "_rdlink._tcp.local.":          "RD Link",
    "_teamviewer._tcp.local.":      "TeamViewer",
    "_ipp._tcp.local.":             "Printer (IPP)",
    "_ipps._tcp.local.":            "Printer (IPPS)",
    "_printer._tcp.local.":         "Printer",
    "_pdl-datastream._tcp.local.":  "Printer (PDL)",
    "_scanner._tcp.local.":         "Scanner",
    "_uscan._tcp.local.":           "USB Scanner",
    "_smb._tcp.local.":             "File Share (SMB)",
    "_afpovertcp._tcp.local.":      "File Share (AFP)",
    "_ftp._tcp.local.":             "FTP",
    "_ssh._tcp.local.":             "SSH",
    "_airplay._tcp.local.":         "AirPlay",
    "_raop._tcp.local.":            "AirPlay Audio",
    "_companion-link._tcp.local.":  "Apple Companion Link",
    "_googlecast._tcp.local.":      "Chromecast",
    "_spotify-connect._tcp.local.": "Spotify Connect",
    "_daap._tcp.local.":            "iTunes Share",
    "_apple-mobdev2._tcp.local.":   "Apple Mobile",
    "_device-info._tcp.local.":     "Device Info",
}


def _web_url(service_type: str, addresses: list[str], port: int) -> str | None:
    if not addresses:
        return None
    ip = addresses[0]
    if "_https" in service_type or "_ipps" in service_type:
        scheme, default_port = "https", 443
    elif any(k in service_type for k in ("_http", "_ipp", "_printer", "_pdl",
                                          "_daap", "_airplay", "_googlecast",
                                          "_rdlink", "_rfb")):
        scheme, default_port = "http", 80
    elif "_ftp" in service_type:
        scheme, default_port = "ftp", 21
    else:
        return None
    return f"{scheme}://{ip}" if port == default_port else f"{scheme}://{ip}:{port}"


def _short_name(full_name: str) -> str:
    for stype in SERVICE_TYPES:
        suffix = "." + stype.rstrip(".")
        if full_name.endswith(suffix):
            full_name = full_name[: -len(suffix)]
            break
    return full_name.split(".")[0]


# ── mDNS listener ────────────────────────────────────────────────────────────

class _Listener(ServiceListener):
    def __init__(self, cb):
        self._cb = cb

    def add_service(self, zc, stype, name):
        info = zc.get_service_info(stype, name)
        if not info:
            return
        ipv4 = [socket.inet_ntoa(a) for a in info.addresses if len(a) == 4]
        self._cb("add", {
            "name": name,
            "service_type": stype,
            "server": info.server or "",
            "port": info.port,
            "addresses": ipv4,
        })

    def remove_service(self, zc, stype, name):
        self._cb("remove", {"name": name, "service_type": stype})

    def update_service(self, zc, stype, name):
        self.add_service(zc, stype, name)


# ── Main application ─────────────────────────────────────────────────────────

class BonjourBrowser(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("Bonjour Browser")
        self.geometry("900x560")
        self.minsize(600, 360)

        self._zc: Zeroconf | None = None
        self._scanning = False
        self._devices: dict[str, dict] = {}   # name -> device info
        self._cat_nodes: dict[str, str] = {}   # label -> tree iid
        self._dev_nodes: dict[str, str] = {}   # name  -> tree iid

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Toolbar
        bar = ttk.Frame(self)
        bar.pack(fill=tk.X, padx=6, pady=(6, 2))

        self.btn_scan = ttk.Button(bar, text="▶  Scan", width=10, command=self._start_scan)
        self.btn_stop = ttk.Button(bar, text="■  Stop", width=10, command=self._stop_scan,
                                   state=tk.DISABLED)
        self.btn_scan.pack(side=tk.LEFT, padx=(0, 4))
        self.btn_stop.pack(side=tk.LEFT, padx=(0, 10))
        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, pady=2, padx=4)

        self._status = tk.StringVar(value="Ready — click Scan to start.")
        ttk.Label(bar, textvariable=self._status, foreground="#444").pack(side=tk.LEFT, padx=6)

        ttk.Label(bar, text="Double-click a device to open in browser",
                  foreground="#999", font=("Segoe UI", 8)).pack(side=tk.RIGHT, padx=8)

        # Device tree with columns
        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)

        cols = ("ip", "port", "service")
        self._tree = ttk.Treeview(tree_frame, columns=cols, show="tree headings",
                                  selectmode="browse")

        # Tree (name) column
        self._tree.heading("#0",      text="Device Name",  anchor=tk.W)
        self._tree.heading("ip",      text="IP Address",   anchor=tk.W)
        self._tree.heading("port",    text="Port",         anchor=tk.W)
        self._tree.heading("service", text="Service Type", anchor=tk.W)

        self._tree.column("#0",      width=260, minwidth=180, stretch=True)
        self._tree.column("ip",      width=150, minwidth=130, stretch=True)
        self._tree.column("port",    width=60,  minwidth=50,  stretch=False)
        self._tree.column("service", width=200, minwidth=150, stretch=True)

        vsb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL,   command=self._tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        self._tree.pack(fill=tk.BOTH, expand=True)

        self._tree.tag_configure("cat", font=("Segoe UI", 9, "bold"), foreground="#0078d7")
        self._tree.tag_configure("dev", font=("Segoe UI", 9))

        # Double-click opens in browser
        self._tree.bind("<Double-Button-1>", self._on_double_click)

        # Status bar
        sbar = ttk.Frame(self, relief=tk.SUNKEN)
        sbar.pack(fill=tk.X, side=tk.BOTTOM)
        self._count_var = tk.StringVar(value="No devices found.")
        ttk.Label(sbar, textvariable=self._count_var, anchor=tk.W,
                  foreground="#555").pack(side=tk.LEFT, padx=6, pady=1)

    # ── Scan ──────────────────────────────────────────────────────────────

    def _start_scan(self):
        # If already scanning, stop the current session first then restart
        if self._scanning:
            self._stop_scan(silent=True)

        self._scanning = True
        self.btn_stop.configure(state=tk.NORMAL)
        self._status.set("Scanning…")

        for iid in self._tree.get_children():
            self._tree.delete(iid)
        self._devices.clear()
        self._cat_nodes.clear()
        self._dev_nodes.clear()
        self._count_var.set("Scanning…")

        threading.Thread(target=self._scan_thread, daemon=True).start()

    def _scan_thread(self):
        listener = _Listener(self._on_device_event)
        self._zc = Zeroconf()
        [ServiceBrowser(self._zc, t, listener) for t in SERVICE_TYPES]

    def _stop_scan(self, silent: bool = False):
        self._scanning = False
        if self._zc:
            old_zc = self._zc
            self._zc = None
            threading.Thread(target=old_zc.close, daemon=True).start()
        self.btn_stop.configure(state=tk.DISABLED)
        if not silent:
            n = len(self._devices)
            self._status.set(f"Stopped.  {n} device(s) found.")
            self._count_var.set(f"{n} device(s) found.")

    # ── Device events ─────────────────────────────────────────────────────

    def _on_device_event(self, action: str, info: dict):
        self.after(0, self._apply_event, action, info)

    def _apply_event(self, action: str, info: dict):
        name = info["name"]
        if action == "remove":
            iid = self._dev_nodes.pop(name, None)
            if iid:
                self._tree.delete(iid)
            self._devices.pop(name, None)
        else:
            self._devices[name] = info
            stype = info["service_type"]
            label = SERVICE_LABELS.get(stype, stype)

            if label not in self._cat_nodes:
                cat = self._tree.insert("", tk.END, text=f"  {label}",
                                        open=True, tags=("cat",),
                                        values=("", "", ""))
                self._cat_nodes[label] = cat

            cat = self._cat_nodes[label]
            ip   = info["addresses"][0] if info["addresses"] else ""
            port = str(info["port"]) if info["port"] else ""
            short = _short_name(name)

            if name not in self._dev_nodes:
                iid = self._tree.insert(cat, tk.END,
                                        text=f"  {short}",
                                        tags=("dev",),
                                        values=(ip, port, stype.rstrip(".")),
                                        iid=None)
                # Store device key in the item's hidden tag list
                self._tree.item(iid, tags=("dev", name))
                self._dev_nodes[name] = iid

        n = len(self._devices)
        self._count_var.set(f"{n} device(s) found.")
        if self._scanning:
            self._status.set(f"Scanning…  {n} device(s) found so far.")

    # ── Double-click ──────────────────────────────────────────────────────

    def _on_double_click(self, event):
        iid = self._tree.identify_row(event.y)
        if not iid:
            return
        tags = self._tree.item(iid, "tags")
        # Category nodes have tag "cat"; device nodes have ("dev", device_name)
        if not tags or tags[0] == "cat":
            return
        name = tags[1] if len(tags) > 1 else None
        if not name:
            return
        device = self._devices.get(name)
        if not device:
            return

        url = _web_url(device["service_type"], device["addresses"], device["port"])
        if url:
            import webbrowser
            webbrowser.open(url)
            self._status.set(f"Opened: {url}")
        else:
            self._status.set(f"No web interface for {_short_name(name)}")

    # ── Close ─────────────────────────────────────────────────────────────

    def _on_close(self):
        self._stop_scan()
        self.after(300, self.destroy)


if __name__ == "__main__":
    app = BonjourBrowser()
    app.mainloop()
