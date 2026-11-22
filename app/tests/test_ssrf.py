import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.core.config import Settings


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "LOCALHOST.",
        "a.localhost",
        "127.0.0.1",
        "127.1",
        "169.254.169.254",
        "10.1.2.3",
        "172.16.0.1",
        "192.168.1.1",
        "100.64.0.1",
        "0.0.0.0",  # noqa: S104 -- negative SSRF fixture, never a bind address.
        "224.0.0.1",
        "240.0.0.1",
        "[::1]",
        "[fc00::1]",
        "[fd00::1]",
        "[fe80::1]",
        "[::]",
        "[ff02::1]",
        "[::ffff:127.0.0.1]",
        "[::ffff:10.0.0.1]",
        "2130706433",
        "0x7f000001",
        "017700000001",
        "0177.0.0.1",
        "0x7f.0.0.1",
        "127.000.000.001",
    ],
)
def test_ssrf_blocks_private_special_and_encoded_hosts_without_dns(host, monkeypatch):
    from app.core import ssrf

    monkeypatch.setattr(ssrf, "resolve_addresses", lambda *args: pytest.fail("Unsafe literal reached DNS"))
    with pytest.raises(ssrf.UnsafeTarget):
        ssrf.validate_target("https://" + host + "/hook", Settings(_env_file=None))


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "ftp://example.com",
        "https://user:pass@example.com",
        "https://example.com:8443",
        "https://example.com/#fragment",
        "https://example.com:bad",
        "https://%31%32%37.0.0.1",
        "https://[fe80::1%25eth0]",
        "https://example.com/\r\nheader",
    ],
)
def test_ssrf_rejects_unsafe_url_shapes(url):
    from app.core.ssrf import UnsafeTarget, validate_target

    with pytest.raises(UnsafeTarget):
        validate_target(url, Settings(_env_file=None))


def test_ssrf_checks_every_dns_answer_and_public_addresses(monkeypatch):
    from app.core import ssrf

    settings = Settings(_env_file=None)
    monkeypatch.setattr(ssrf, "resolve_addresses", lambda *args: ["8.8.8.8", "10.0.0.1"])
    with pytest.raises(ssrf.UnsafeTarget):
        ssrf.validate_target("https://example.com/hook", settings)
    monkeypatch.setattr(ssrf, "resolve_addresses", lambda *args: ["8.8.8.8", "2606:4700:4700::1111"])
    assert ssrf.validate_target("https://example.com/hook", settings) == "https://example.com/hook"
    assert ssrf.validate_target("https://8.8.8.8/hook", settings)
    monkeypatch.setattr(ssrf, "resolve_addresses", lambda *args: [])
    with pytest.raises(ssrf.UnsafeTarget):
        ssrf.validate_target("https://example.com/hook", settings)


def test_insecure_private_override_is_explicit_and_forbidden_nonlocally():
    from app.core.ssrf import validate_target

    local = Settings(_env_file=None, webhook_allow_insecure_targets=True, webhook_extra_allowed_ports=[8080])
    assert validate_target("http://127.0.0.1:8080/hook", local)
    for env in ("production", "staging", "custom"):
        with pytest.raises(ValidationError, match="webhook"):
            Settings(
                _env_file=None,
                environment=env,
                secret_key="x" * 32,
                allow_mock_providers=True,
                shipping_provider="manual",
                credentials_encryption_key=Fernet.generate_key().decode(),
                webhook_allow_insecure_targets=True,
            )


def test_dns_resolution_deduplicates_and_os_failures_are_generic(monkeypatch):
    from app.core import ssrf

    monkeypatch.setattr(
        ssrf.socket,
        "getaddrinfo",
        lambda *a, **kw: [(None, None, None, None, ("8.8.8.8", 443)), (None, None, None, None, ("8.8.8.8", 443))],
    )
    assert ssrf.resolve_addresses("example.com", 443) == ["8.8.8.8"]
    monkeypatch.setattr(
        ssrf.socket, "getaddrinfo", lambda *a, **kw: (_ for _ in ()).throw(OSError("private DNS diagnostic"))
    )
    with pytest.raises(ssrf.UnsafeTarget, match="Webhook target is not allowed") as error:
        ssrf.validate_target("https://example.com", Settings(_env_file=None))
    assert "private DNS" not in str(error.value)
    for ports in ([0], [65536]):
        with pytest.raises(ValidationError, match="port"):
            Settings(_env_file=None, webhook_extra_allowed_ports=ports)
    with pytest.raises(ValidationError, match="lease"):
        Settings(_env_file=None, webhook_lease_seconds=1)


def test_explicit_zero_port_cannot_fall_back_to_https_default(monkeypatch):
    from app.core import ssrf

    monkeypatch.setattr(ssrf, "resolve_addresses", lambda *args: ["8.8.8.8"])
    with pytest.raises(ssrf.UnsafeTarget):
        ssrf.validate_target("https://seller.example:0/hook", Settings(_env_file=None))
