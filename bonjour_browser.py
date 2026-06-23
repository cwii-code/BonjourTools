#!/usr/bin/env python3
"""
Bonjour Browser - IE-style GUI with embedded web view.
Left panel : discovered mDNS/Bonjour devices (tree view)
Right panel: embedded web browser for selected device

Requirements:
    pip install zeroconf tkinterweb
"""

import socket
import threading
import tkinter as tk
from tkinter import ttk
from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

try:
    from tkinterweb import HtmlFrame
    HAS_WEBVIEW = True
except ImportError:
    HAS_WEBVIEW = False

# ── Service types to scan ────────────────────────────────────────────────────

SERVICE_TYPES = [
    "_http._tcp.local.",
    "_https._tcp.local.",
    "_ipp._tcp.local.",
    "_ipps._tcp.local.",
    "_printer._tcp.local.",
    "_pdl-datastream._tcp.local.",
    "_smb._tcp.local.",
    "_afpovertcp._tcp.local.",
    "_ftp._tcp.local.",
    "_ssh._tcp.local.",
    "_airplay._tcp.local.",
    "_raop._tcp.local.",
    "_googlecast._tcp.local.",
    "_spotify-connect._tcp.local.",
    "_daap._tcp.local.",
    "_apple-mobdev2._tcp.local.",
    "_device-info._tcp.local.",
]

SERVICE_LABELS = {
    "_http._tcp.local.":        "Web (HTTP)",
    "_https._tcp.local.":       "Web (HTTPS)",
    "_ipp._tcp.local.":         "Printer (IPP)",
    "_ipps._tcp.local.":        "Printer (IPPS)",
    "_printer._tcp.local.":     "Printer",
    "_pdl-datastream._tcp.local.": "Printer (PDL)",
    "_smb._tcp.local.":         "File Share (SMB)",
    "_afpovertcp._tcp.local.":  "File Share (AFP)",
    "_ftp._tcp.local.":         "FTP",
    "_ssh._tcp.local.":         "SSH",
    "_airplay._tcp.local.":     "AirPlay",
    "_raop._tcp.local.":        "AirPlay Audio",
    "_googlecast._tcp.local.":  "Chromecast",
    "_spotify-connect._tcp.local.": "Spotify Connect",
    "_daap._tcp.local.":        "iTunes Share",
    "_apple-mobdev2._tcp.local.": "Apple Mobile",
    "_device-info._tcp.local.": "Device Info",
}


def _web_url(service_type: str, addresses: list[str], port: int) -> str | None:
    if not addresses:
        return None
    ip = addresses[0]
    if "_https" in service_type or "_ipps" in service_type:
        scheme, default_port = "https", 443
    elif "_http" in service_type or "_ipp" in service_type or "_printer" in service_type or "_pdl" in service_type:
        scheme, default_port = "http", 80
    elif "_ftp" in service_type:
        scheme, default_port = "ftp", 21
    elif "_daap" in service_type or "_airplay" in service_type or "_googlecast" in service_type:
        scheme, default_port = "http", 80  # most have a status page
    else:
        return None
    return f"{scheme}://{ip}" if port == default_port else f"{scheme}://{ip}:{port}"


def _short_name(full_name: str) -> str:
    """Strip service-type suffix from mDNS name."""
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
    _WELCOME_HTML = """
    <html><body style="font-family:Segoe UI,Arial,sans-serif;
                        display:flex;align-items:center;
                        justify-content:center;height:90vh;margin:0;
                        background:#f8f8f8;color:#555;">
      <div style="text-align:center">
        <h2 style="color:#0078d7">Bonjour Browser</h2>
        <p>Click <b>Scan</b> to discover devices on your network.<br>
           Select a device on the left to open its web page here.</p>
      </div>
    </body></html>"""

    def __init__(self):
        super().__init__()
        self.title("Bonjour Browser")
        self.geometry("1200x700")
        self.minsize(800, 500)

        self._zc: Zeroconf | None = None
        self._scanning = False
        self._devices: dict[str, dict] = {}        # name  -> device
        self._cat_nodes: dict[str, str] = {}        # label -> tree iid
        self._dev_nodes: dict[str, str] = {}        # name  -> tree iid

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self):
        self._build_toolbar()
        self._build_main_pane()
        self._build_statusbar()

    def _build_toolbar(self):
        bar = ttk.Frame(self, relief=tk.FLAT)
        bar.pack(fill=tk.X, padx=6, pady=(6, 2))

        self.btn_scan = ttk.Button(bar, text="▶  Scan",  width=10, command=self._start_scan)
        self.btn_stop = ttk.Button(bar, text="■  Stop",  width=10, command=self._stop_scan,
                                   state=tk.DISABLED)
        self.btn_scan.pack(side=tk.LEFT, padx=(0, 4))
        self.btn_stop.pack(side=tk.LEFT, padx=(0, 10))

        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, pady=2, padx=4)

        self._status = tk.StringVar(value="Ready — click Scan to start.")
        ttk.Label(bar, textvariable=self._status, foreground="#444").pack(side=tk.LEFT, padx=6)

    def _build_main_pane(self):
        pane = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        pane.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)

        # ── Left: device tree ─────────────────────────────────────────────
        left = ttk.Frame(pane, width=280)
        left.pack_propagate(False)
        pane.add(left, weight=1)

        ttk.Label(left, text="Local Network Devices",
                  font=("Segoe UI", 9, "bold"), foreground="#333").pack(
                  anchor=tk.W, padx=6, pady=(2, 2))

        tree_wrap = ttk.Frame(left)
        tree_wrap.pack(fill=tk.BOTH, expand=True)

        self._tree = ttk.Treeview(tree_wrap, selectmode="browse", show="tree")
        vsb = ttk.Scrollbar(tree_wrap, orient=tk.VERTICAL, command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._tree.pack(fill=tk.BOTH, expand=True)
        self._tree.tag_configure("cat", font=("Segoe UI", 9, "bold"), foreground="#0078d7")
        self._tree.tag_configure("dev", font=("Segoe UI", 9))
        self._tree.bind("<<TreeviewSelect>>", self._on_select)

        # ── Right: URL bar + web view ─────────────────────────────────────
        right = ttk.Frame(pane)
        pane.add(right, weight=5)

        url_bar = ttk.Frame(right)
        url_bar.pack(fill=tk.X, padx=4, pady=(2, 2))

        ttk.Button(url_bar, text="◀", width=3, command=self._go_back).pack(side=tk.LEFT)
        ttk.Button(url_bar, text="▶", width=3, command=self._go_forward).pack(side=tk.LEFT, padx=(2, 0))
        ttk.Button(url_bar, text="↺", width=3, command=self._go_reload).pack(side=tk.LEFT, padx=(2, 6))

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
            msg_frame = ttk.Frame(right, relief=tk.SUNKEN)
            msg_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
            ttk.Label(
                msg_frame,
                text=(
                    "tkinterweb is not installed.\n\n"
                    "Install it to enable the embedded web view:\n\n"
                    "    pip install tkinterweb\n\n"
                    "Without it, clicking a device will open\n"
                    "your default browser instead."
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
        if self._scanning:
            return
        self._scanning = True
        self.btn_scan.configure(state=tk.DISABLED)
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

    def _stop_scan(self):
        self._scanning = False
        if self._zc:
            threading.Thread(target=self._zc.close, daemon=True).start()
            self._zc = None
        self.btn_scan.configure(state=tk.NORMAL)
        self.btn_stop.configure(state=tk.DISABLED)
        n = len(self._devices)
        self._status.set(f"Stopped. {n} device(s) found.")
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
                                        open=True, tags=("cat",))
                self._cat_nodes[label] = cat

            cat = self._cat_nodes[label]
            ip = info["addresses"][0] if info["addresses"] else "?"
            short = _short_name(name)

            if name not in self._dev_nodes:
                iid = self._tree.insert(cat, tk.END,
                                        text=f"  {short}   {ip}",
                                        tags=("dev",),
                                        values=(name,))
                self._dev_nodes[name] = iid

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
        name = vals[0]
        device = self._devices.get(name)
        if not device:
            return

        url = _web_url(device["service_type"], device["addresses"], device["port"])
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
        label = SERVICE_LABELS.get(device["service_type"], device["service_type"])
        ip    = ", ".join(device["addresses"]) if device["addresses"] else "N/A"
        short = _short_name(device["name"])
        html  = f"""
        <html><body style="font-family:Segoe UI,Arial,sans-serif;
                           padding:32px;color:#333;background:#fff">
          <h2 style="color:#0078d7;margin-bottom:4px">{short}</h2>
          <p style="color:#888;margin-top:0">{label}</p>
          <hr style="border:none;border-top:1px solid #eee;margin:16px 0">
          <table style="border-collapse:collapse;font-size:14px">
            <tr><td style="color:#888;padding:4px 24px 4px 0">Host</td>
                <td><b>{device['server']}</b></td></tr>
            <tr><td style="color:#888;padding:4px 24px 4px 0">IP Address</td>
                <td>{ip}</td></tr>
            <tr><td style="color:#888;padding:4px 24px 4px 0">Port</td>
                <td>{device['port']}</td></tr>
            <tr><td style="color:#888;padding:4px 24px 4px 0">Service</td>
                <td>{device['service_type']}</td></tr>
          </table>
          <p style="color:#aaa;margin-top:32px;font-size:13px">
            This service type does not have a web interface.
          </p>
        </body></html>"""
        self._url_var.set(f"bonjour://{device['server']}")
        if self._webview:
            self._webview.load_html(html)

    # ── Close ─────────────────────────────────────────────────────────────

    def _on_close(self):
        self._stop_scan()
        self.after(300, self.destroy)


if __name__ == "__main__":
    app = BonjourBrowser()
    app.mainloop()
