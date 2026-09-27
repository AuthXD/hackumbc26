"""Build the phone camera link without exposing unrelated network details."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def local_ipv4() -> str | None:
    """Return the preferred private IPv4 address without sending network traffic."""
    candidates: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 9))
            candidates.append(sock.getsockname()[0])
    except OSError:
        pass
    try:
        candidates.extend(info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    except OSError:
        pass
    for candidate in candidates:
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if address.is_private and not address.is_loopback:
            return candidate
    return None


def phone_mode_url(base: str) -> str | None:
    """Validate an HTTP(S) base URL and add the dedicated phone-camera query flag."""
    try:
        parsed = urlsplit(base.strip())
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["phone"] = "1"
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", urlencode(query), ""))


def phone_link(public_url: str, port: int = 5173) -> dict:
    configured = phone_mode_url(public_url) if public_url else None
    address = local_ipv4()
    lan = phone_mode_url(f"http://{address}:{port}") if address else None
    selected = configured or lan
    return {
        "url": selected,
        "lanUrl": lan,
        "secure": bool(selected and selected.startswith("https://")),
        "source": "configured" if configured else "lan" if lan else "unavailable",
    }
