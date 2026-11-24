def test_e2e_runner_reports_failures_without_hiding_them(capsys):
    from scripts.e2e_reliability import experiment

    assert experiment("control", lambda: "verified")
    assert not experiment("failure", lambda: (_ for _ in ()).throw(AssertionError("wrong stock")))
    output = capsys.readouterr().out
    assert "PASS control" in output and "FAIL failure" in output and "wrong stock" in output


def test_seller_acceptance_inventory_and_independent_signature_check():
    import hashlib
    import hmac
    import json

    import pytest

    from scripts.e2e_seller_integrations import CASES, verify_received

    assert set(CASES) == {"E19", "E22", "E23", "E24", "E25", "E26", "E27", "E29"}
    raw = json.dumps({"id": "event-fixture", "type": "ping", "data": {}}, separators=(",", ":"))
    secret = "synthetic-signature-fixture"
    signature = hmac.new(secret.encode(), ("123." + raw).encode(), hashlib.sha256).hexdigest()
    receipt = {
        "body": raw,
        "headers": {
            "X-OOS-Signature": "t=123,v1=" + signature,
            "X-OOS-Timestamp": "123",
            "X-OOS-Event-Id": "event-fixture",
            "X-OOS-Event-Type": "ping",
        },
        "status": 204,
    }
    assert verify_received([receipt], secret)[0]["id"] == "event-fixture"
    receipt["body"] += " "
    with pytest.raises(RuntimeError, match="signature"):
        verify_received([receipt], secret)


def test_seller_cleanup_disables_only_its_temporary_platform_principal(db):
    from unittest.mock import MagicMock

    from app.models.entities import User
    from app.models.enums import UserRole
    from app.tests.conftest import TestingSessionLocal
    from scripts.e2e_seller_integrations import SellerHarness

    principal = User(
        tenant_id=None,
        is_platform_admin=True,
        role=UserRole.ADMIN,
        email="p13-root-cleanup01@example.com",
        full_name="Proof platform",
        hashed_password="fixture",
        is_active=True,
    )
    unrelated = User(
        tenant_id=None,
        is_platform_admin=True,
        role=UserRole.ADMIN,
        email="unrelated-platform@example.com",
        full_name="Unrelated platform",
        hashed_password="fixture",
        is_active=True,
    )
    db.add_all([principal, unrelated])
    db.commit()
    harness = SellerHarness.__new__(SellerHarness)
    harness.run_id = "cleanup01"
    harness.platform_user_id = principal.id
    harness.endpoint_ids = []
    harness.sessions = TestingSessionLocal
    harness.sink = harness.mail = harness.client = harness.engine = MagicMock()
    harness.cleanup()
    db.expire_all()
    assert not db.get(User, principal.id).is_active
    assert db.get(User, unrelated.id).is_active
