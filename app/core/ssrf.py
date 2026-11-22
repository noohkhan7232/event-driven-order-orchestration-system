"""Validate all DNS answers and return a numeric peer for pinned HTTP delivery."""

import ipaddress
import re
import socket
from urllib.parse import urlsplit

from app.core.config import Settings
from app.core.exceptions import DomainError


class UnsafeTarget(DomainError):
    code = "unsafe_webhook_url"

    def __init__(self) -> None:
        super().__init__("Webhook target is not allowed")


def resolve_addresses(host: str, port: int) -> list[str]:
    return list({str(row[4][0]) for row in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})


def public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return bool(
        address.is_global
        and not address.is_multicast
        and not address.is_reserved
        and not (isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped)
    )


def resolve_target(value: str, settings: Settings) -> tuple[str, str]:
    try:
        if not value or len(value) > 2048 or any(ord(c) < 33 or ord(c) == 127 for c in value) or "\\" in value:
            raise ValueError
        parsed = urlsplit(value)
        host = (parsed.hostname or "").encode("idna").decode().lower().rstrip(".")
        insecure = settings.webhook_allow_insecure_targets
        if parsed.scheme not in ({"https", "http"} if insecure else {"https"}):
            raise ValueError
        if not host or "%" in host or parsed.username is not None or parsed.password is not None or parsed.fragment:
            raise ValueError
        port = parsed.port
        if port is None:
            port = 443 if parsed.scheme == "https" else 80
        if port not in {443, *settings.webhook_extra_allowed_ports}:
            raise ValueError
        if host == "localhost" or host.endswith(".localhost"):
            if not insecure:
                raise ValueError
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            # Reject inet_aton-style short/decimal/hex/octal encodings before DNS.
            if re.fullmatch(r"(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+))*", host, re.I):
                raise ValueError from None
            addresses = resolve_addresses(host, port)
        else:
            addresses = [str(literal)]
        addresses = [str(ipaddress.ip_address(address)) for address in addresses]
        if not addresses or (not insecure and not all(public_address(address) for address in addresses)):
            raise ValueError
    except (ValueError, UnicodeError, OSError):
        raise UnsafeTarget() from None
    return value, addresses[0]


def validate_target(value: str, settings: Settings) -> str:
    return resolve_target(value, settings)[0]
