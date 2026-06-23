#!/usr/bin/env python3
"""
Bonjour/mDNS device scanner - replaces Apple Bonjour Browser (IE-based).
Discovers devices and services on the local network using mDNS/DNS-SD.

Requirements:
    pip install zeroconf
"""

import socket
import time
import argparse
from datetime import datetime
from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

# Common Bonjour service types to scan
DEFAULT_SERVICE_TYPES = [
    "_http._tcp.local.",
    "_https._tcp.local.",
    "_smb._tcp.local.",
    "_afpovertcp._tcp.local.",
    "_ftp._tcp.local.",
    "_ssh._tcp.local.",
    "_ipp._tcp.local.",
    "_ipps._tcp.local.",
    "_printer._tcp.local.",
    "_pdl-datastream._tcp.local.",
    "_airplay._tcp.local.",
    "_raop._tcp.local.",
    "_apple-mobdev2._tcp.local.",
    "_sleep-proxy._udp.local.",
    "_googlecast._tcp.local.",
    "_spotify-connect._tcp.local.",
    "_daap._tcp.local.",
    "_device-info._tcp.local.",
    "_services._dns-sd._udp.local.",
]

SERVICE_LABELS = {
    "_http._tcp.local.": "Web (HTTP)",
    "_https._tcp.local.": "Web (HTTPS)",
    "_smb._tcp.local.": "Windows File Share (SMB)",
    "_afpovertcp._tcp.local.": "Apple File Share (AFP)",
    "_ftp._tcp.local.": "FTP",
    "_ssh._tcp.local.": "SSH",
    "_ipp._tcp.local.": "Printer (IPP)",
    "_ipps._tcp.local.": "Printer (IPPS)",
    "_printer._tcp.local.": "Printer",
    "_pdl-datastream._tcp.local.": "Printer (PDL)",
    "_airplay._tcp.local.": "AirPlay",
    "_raop._tcp.local.": "AirPlay Audio (RAOP)",
    "_apple-mobdev2._tcp.local.": "Apple Mobile Device",
    "_sleep-proxy._udp.local.": "Apple Sleep Proxy",
    "_googlecast._tcp.local.": "Google Cast / Chromecast",
    "_spotify-connect._tcp.local.": "Spotify Connect",
    "_daap._tcp.local.": "iTunes Music Share (DAAP)",
    "_device-info._tcp.local.": "Device Info",
}


class DeviceListener(ServiceListener):
    def __init__(self, verbose: bool = False):
        self.devices: dict[str, dict] = {}
        self.verbose = verbose

    def add_service(self, zc: Zeroconf, service_type: str, name: str) -> None:
        info = zc.get_service_info(service_type, name)
        if not info:
            return

        addresses = [socket.inet_ntoa(addr) for addr in info.addresses if len(addr) == 4]
        addresses += [socket.inet_ntop(socket.AF_INET6, addr) for addr in info.addresses if len(addr) == 16]

        props = {k.decode(): v.decode() if isinstance(v, bytes) else v
                 for k, v in (info.properties or {}).items()
                 if isinstance(k, bytes)}

        label = SERVICE_LABELS.get(service_type, service_type)
        entry = {
            "name": name,
            "service_type": service_type,
            "service_label": label,
            "server": info.server,
            "port": info.port,
            "addresses": addresses,
            "properties": props,
            "found_at": datetime.now().strftime("%H:%M:%S"),
        }
        self.devices[name] = entry
        self._print_device(entry)

    def remove_service(self, zc: Zeroconf, service_type: str, name: str) -> None:
        if name in self.devices:
            print(f"  [-] Gone: {name}")
            del self.devices[name]

    def update_service(self, zc: Zeroconf, service_type: str, name: str) -> None:
        self.add_service(zc, service_type, name)

    def _print_device(self, d: dict) -> None:
        ips = ", ".join(d["addresses"]) if d["addresses"] else "unknown"
        print(f"\n  [+] {d['name']}")
        print(f"      Type   : {d['service_label']}")
        print(f"      Host   : {d['server']}  Port: {d['port']}")
        print(f"      IP(s)  : {ips}")
        if self.verbose and d["properties"]:
            for k, v in d["properties"].items():
                print(f"      {k:<10}: {v}")


def scan(timeout: int = 10, service_types: list[str] | None = None, verbose: bool = False) -> list[dict]:
    service_types = service_types or DEFAULT_SERVICE_TYPES

    print(f"Scanning local network for Bonjour/mDNS devices ({timeout}s)...\n")
    print(f"Services: {len(service_types)} types")
    print("-" * 60)

    zc = Zeroconf()
    listener = DeviceListener(verbose=verbose)
    browsers = [ServiceBrowser(zc, stype, listener) for stype in service_types]

    try:
        time.sleep(timeout)
    except KeyboardInterrupt:
        print("\nScan interrupted.")
    finally:
        zc.close()

    return list(listener.devices.values())


def print_summary(devices: list[dict]) -> None:
    print("\n" + "=" * 60)
    print(f"SUMMARY: {len(devices)} device(s) found")
    print("=" * 60)
    if not devices:
        print("No devices found. Make sure you are on the same LAN/Wi-Fi.")
        return
    for d in sorted(devices, key=lambda x: x["addresses"][0] if x["addresses"] else ""):
        ips = ", ".join(d["addresses"]) if d["addresses"] else "N/A"
        print(f"  {ips:<18}  {d['server']:<30}  {d['service_label']}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan local network for Bonjour/mDNS devices (replaces Apple Bonjour Browser)"
    )
    parser.add_argument("-t", "--timeout", type=int, default=10,
                        help="Scan duration in seconds (default: 10)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Show service TXT record properties")
    parser.add_argument("--types", nargs="+", metavar="SERVICE_TYPE",
                        help="Custom service types to scan (e.g. _http._tcp.local.)")
    args = parser.parse_args()

    devices = scan(timeout=args.timeout, service_types=args.types, verbose=args.verbose)
    print_summary(devices)


if __name__ == "__main__":
    main()
