"""Where this Mac can be reached from, when you let it be reached at all.

Serving on the network needs two things the loopback server never did: a name
the phone can type, and the exact set of Host headers that name arrives as.
Both come from here, and both are empty unless LAN serving is switched on.
"""
from __future__ import annotations

import subprocess


def lan_ips() -> list[str]:
    """This Mac's addresses on the local network, best first.

    Wi-Fi and Ethernet only: `ipconfig getifaddr` answers for a named interface
    and says nothing for one that is down, which keeps VPN and container
    addresses -- the ones a phone cannot reach -- out of the list.
    """
    out: list[str] = []
    for iface in ("en0", "en1", "en2"):
        try:
            ip = subprocess.run(["ipconfig", "getifaddr", iface], capture_output=True,
                                text=True, timeout=3).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            continue
        if ip and ip not in out:
            out.append(ip)
    return out


def bonjour_name() -> str | None:
    """The `something.local` name this Mac answers to, which survives a new lease
    where the address does not -- so it is the one worth printing.

    From `scutil`, not `socket.gethostname()`: the latter hands back whatever the
    router called this Mac in its DHCP lease (here, `MacBookPro.attlocal.net`),
    and sticking `.local` on the end of that gives a name nothing resolves.
    """
    try:
        name = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True,
                              text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return f"{name}.local" if name else None


def reachable_at(port: int) -> list[str]:
    """Every address the phone might reach this server on, as `host:port`."""
    hosts = [h for h in (bonjour_name(), *lan_ips()) if h]
    return [f"{h}:{port}" for h in hosts]
