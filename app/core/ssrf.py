"""Validate all DNS answers and return a numeric peer for pinned HTTP delivery."""

import ipaddress
import re
import socket
from queue import Empty, Queue
from threading import BoundedSemaphore, Thread
from urllib.parse import urlsplit

from app.core.config import Settings
from app.core.exceptions import DomainError

_dns_slots = BoundedSemaphore(8)
_transition_networks = tuple(
    ipaddress.IPv6Network(prefix) for prefix in ("64:ff9b::/96", "64:ff9b:1::/48", "2002::/16", "2001::/32")
)


class UnsafeTarget(DomainError):
    code = "unsafe_webhook_url"

    def __init__(self) -> None:
        super().__init__("Webhook target is not allowed")


def resolve_addresses(host: str, port: int) -> list[str]:
    return list({str(row[4][0]) for row in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})


def bounded_resolve(host: str, port: int, timeout: float) -> list[str]:
    # getaddrinfo cannot be interrupted portably. Bound callers and outstanding
    # daemon lookups; a stuck OS resolver never creates an unbounded thread pool.
    slots = _dns_slots
    if timeout <= 0 or not slots.acquire(blocking=False):
        raise UnsafeTarget()
    result: Queue[list[str] | BaseException] = Queue(maxsize=1)

    def lookup() -> None:
        try:
            result.put(resolve_addresses(host, port))
        except BaseException as error:
            result.put(error)
        finally:
            slots.release()

    try:
        Thread(target=lookup, name="webhook-dns", daemon=True).start()
    except RuntimeError:
        slots.release()
        raise UnsafeTarget() from None
    try:
        answer = result.get(timeout=timeout)
    except Empty:
        raise UnsafeTarget() from None
    if isinstance(answer, BaseException):
        raise answer
    return answer


def blocked_transition(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # Reject the entire transition ranges, including public embedded IPv4.
    return isinstance(address, ipaddress.IPv6Address) and any(address in network for network in _transition_networks)


def public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return bool(
        not blocked_transition(address)
        and address.is_global
        and not address.is_multicast
        and not address.is_reserved
        and not (isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped)
    )


def resolve_target(value: str, settings: Settings, *, dns_timeout: float | None = None) -> tuple[str, str]:
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
            timeout = settings.webhook_dns_timeout_seconds
            if dns_timeout is not None:
                timeout = min(timeout, dns_timeout)
            addresses = bounded_resolve(host, port, timeout)
        else:
            addresses = [str(literal)]
        addresses = [str(ipaddress.ip_address(address)) for address in addresses]
        if (
            not addresses
            or any(blocked_transition(ipaddress.ip_address(address)) for address in addresses)
            or (not insecure and not all(public_address(address) for address in addresses))
        ):
            raise ValueError
    except (ValueError, UnicodeError, OSError):
        raise UnsafeTarget() from None
    return value, addresses[0]


def validate_target(value: str, settings: Settings) -> str:
    return resolve_target(value, settings)[0]
