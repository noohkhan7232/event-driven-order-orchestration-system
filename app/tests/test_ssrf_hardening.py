import threading
import time

import pytest
from pydantic import ValidationError

from app.core import ssrf
from app.core.config import Settings

TRANSITION_ADDRESSES = (
    "64:ff9b::808:808",
    "64:ff9b::a00:1",
    "64:ff9b:1::808:808",
    "64:ff9b:1::a00:1",
    "2002:0808:0808::1",
    "2002:0a00:0001::1",
    "2001:0000:0808:0808:0:0:f7f7:f7f7",
    "2001:0000:0808:0808:0:0:f5ff:fffe",
)


@pytest.mark.parametrize("address", TRANSITION_ADDRESSES)
@pytest.mark.parametrize("insecure", [False, True])
def test_transition_prefixes_reject_public_and_private_embedded_ipv4(address, insecure):
    settings = Settings(_env_file=None, webhook_allow_insecure_targets=insecure)
    assert not ssrf.public_address(address)
    with pytest.raises(ssrf.UnsafeTarget, match="Webhook target is not allowed"):
        ssrf.validate_target(f"https://[{address}]/hook", settings)


def test_blocked_resolver_returns_generic_error_within_dns_deadline(monkeypatch):
    release = threading.Event()
    entered = threading.Event()

    def blocked(*args, **kwargs):
        entered.set()
        release.wait(1)
        return [(None, None, None, None, ("8.8.8.8", 443))]

    monkeypatch.setattr(ssrf.socket, "getaddrinfo", blocked)
    settings = Settings(_env_file=None, webhook_dns_timeout_seconds=0.05)
    started = time.monotonic()
    try:
        with pytest.raises(ssrf.UnsafeTarget, match="Webhook target is not allowed") as error:
            ssrf.validate_target("https://resolver.invalid/hook", settings)
        assert entered.is_set() and time.monotonic() - started < 0.5
        assert str(error.value) == "Webhook target is not allowed"
    finally:
        release.set()


def test_timed_out_resolvers_have_bounded_background_capacity(monkeypatch):
    release = threading.Event()
    calls = []

    def blocked(*args, **kwargs):
        calls.append(1)
        release.wait(1)
        return [(None, None, None, None, ("8.8.8.8", 443))]

    monkeypatch.setattr(ssrf.socket, "getaddrinfo", blocked)
    settings = Settings(_env_file=None, webhook_dns_timeout_seconds=0.02)
    try:
        for _ in range(10):
            started = time.monotonic()
            with pytest.raises(ssrf.UnsafeTarget):
                ssrf.validate_target("https://resolver.invalid/hook", settings)
            assert time.monotonic() - started < 0.5
        assert 0 < len(calls) <= 8
    finally:
        release.set()


def test_dns_answer_transition_prefix_cannot_hide_among_public_answers(monkeypatch):
    monkeypatch.setattr(ssrf, "resolve_addresses", lambda *args: ["8.8.8.8", "64:ff9b::a00:1"])
    with pytest.raises(ssrf.UnsafeTarget):
        ssrf.validate_target("https://resolver.invalid/hook", Settings(_env_file=None))


def test_sender_caps_dns_by_remaining_total_budget(monkeypatch):
    import httpx

    from app.services.webhook_delivery import send_webhook

    release = threading.Event()

    def blocked(*args, **kwargs):
        release.wait(1)
        return [(None, None, None, None, ("8.8.8.8", 443))]

    monkeypatch.setattr(ssrf.socket, "getaddrinfo", blocked)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: pytest.fail("DNS timeout reached HTTP transport"))
    settings = Settings(
        _env_file=None,
        webhook_timeout_seconds=0.1,
        webhook_total_timeout_seconds=0.2,
        webhook_dns_timeout_seconds=5,
    )
    started = time.monotonic()
    try:
        with pytest.raises(ssrf.UnsafeTarget):
            send_webhook("https://resolver.invalid/hook", b"{}", {}, settings)
        assert time.monotonic() - started < 0.5
    finally:
        release.set()


@pytest.mark.parametrize("value", [0, float("inf"), float("nan")])
def test_dns_timeout_must_be_positive(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, webhook_dns_timeout_seconds=value)
