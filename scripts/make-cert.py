"""Generate Butler's own TLS certificate for LAN / tailnet access.

The microphone is the whole point of Butler, and browsers refuse
`getUserMedia` on a non-localhost origin unless it is HTTPS (a "secure
context"). So a phone or tablet satellite can only work if the daemon serves
over TLS. This writes a self-signed cert+key into the ssl directory
`daemon/paths.py` names (`ssl/` on the legacy layout, `<data>/.butler/ssl`
once migrated; never committed) whose SANs cover every way this machine is reachable -- localhost, its hostname, its
LAN IP, and its tailscale IP -- so the same cert answers on every device.

Run it again whenever the machine's addresses change:
    python -X utf8 scripts\\make-cert.py

The cert is self-signed, so a device shows a warning the first time; accept it
(or install the cert as trusted) and the microphone works. `--tls` on the
daemon (start.bat passes it) then serves https/wss on the same port.
"""
from __future__ import annotations

import shutil
import socket
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from daemon import paths  # noqa: E402

# Where the daemon will LOOK for it -- `<data root>\.butler\ssl` once
# BUTLER_DATA is set, the repo's `ssl\` on the legacy layout. One resolver,
# because a cert written where the daemon does not read is never served and
# nothing reports it (an earlier prototype's 2026-08-03 incident, docs/data-root.md).
SSL = paths.resolve().ssl
DAYS = 825   # the ceiling Safari/Chrome accept for a leaf certificate


def find_openssl() -> str:
    """openssl on PATH, or the copy Git for Windows ships."""
    found = shutil.which("openssl")
    if found:
        return found
    for p in (r"C:\Program Files\Git\mingw64\bin\openssl.exe",
              r"C:\Program Files\Git\usr\bin\openssl.exe",
              r"C:\Program Files (x86)\Git\mingw64\bin\openssl.exe"):
        if Path(p).exists():
            return p
    raise SystemExit(
        "openssl not found. Install Git for Windows (it ships openssl) or "
        "add openssl to PATH, then re-run.")


def local_ipv4s() -> list[str]:
    """Every IPv4 this machine answers on: loopback, LAN, tailscale, virtual.

    A cert that names them all works whether the phone reaches Butler by LAN
    IP or over the tailnet, with no second cert.
    """
    ips = {"127.0.0.1"}
    host = socket.gethostname()
    try:
        for res in socket.getaddrinfo(host, None, socket.AF_INET):
            ips.add(res[4][0])
    except OSError:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))          # no packet sent; just picks the route
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(ips)


def main() -> None:
    openssl = find_openssl()
    host = socket.gethostname()
    ips = local_ipv4s()
    names = ["localhost", host, f"{host}.local"]
    san = ",".join([f"DNS:{n}" for n in names] + [f"IP:{ip}" for ip in ips])

    SSL.mkdir(parents=True, exist_ok=True)
    cert, key = SSL / "cert.pem", SSL / "key.pem"
    cmd = [openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
           "-keyout", str(key), "-out", str(cert), "-days", str(DAYS),
           "-subj", "/CN=butler", "-addext", f"subjectAltName={san}"]
    print(f"[make-cert] {openssl}")
    print(f"[make-cert] SAN: {san}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise SystemExit(f"[make-cert] openssl failed ({result.returncode})")
    print(f"[make-cert] wrote {cert}")
    print(f"[make-cert] wrote {key}")
    print("[make-cert] reach Butler from a device at any of:")
    for ip in ips:
        if ip != "127.0.0.1":
            print(f"             https://{ip}:8140")
    print(f"             https://{host}.local:8140")


if __name__ == "__main__":
    main()
