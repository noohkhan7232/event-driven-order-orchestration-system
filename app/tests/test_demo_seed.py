import pytest
from conftest import TestingSessionLocal

from app.core.config import get_settings
from app.models.entities import ApiKey, Inventory, Product, User
from scripts import seed_demo


def test_demo_seed_is_repeatable_and_only_shows_new_key_once(db, monkeypatch, capsys):
    monkeypatch.setattr(seed_demo, "SessionLocal", TestingSessionLocal)
    seed_demo.main()
    first = capsys.readouterr().out
    assert "Demo API key (shown once): oos_live_" in first
    secret = first.split("Demo API key (shown once): ")[1].splitlines()[0]
    seed_demo.main()
    assert "oos_live_" not in capsys.readouterr().out
    assert db.query(User).count() == 4 and db.query(ApiKey).count() == 1
    root = db.query(User).filter_by(email="platform@orders.local").one()
    assert root.is_platform_admin and root.tenant_id is None
    assert db.query(Inventory).count() == 3 and db.query(Product).count() == 5
    key = db.query(ApiKey).one()
    assert secret != key.key_hash and secret not in str(key.__dict__)


def test_demo_seed_refuses_nonlocal_environment(monkeypatch):
    monkeypatch.setattr(
        seed_demo, "get_settings", lambda: get_settings().model_copy(update={"environment": "production"})
    )
    with pytest.raises(RuntimeError, match="local only"):
        seed_demo.main()
