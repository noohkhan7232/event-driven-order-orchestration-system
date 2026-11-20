import pytest
from pydantic import ValidationError

from app.core.config import Settings


@pytest.mark.parametrize("environment", ["production", "staging", "custom"])
@pytest.mark.parametrize("secret", ["change-me", "change-me-in-production", "short-secret"])
def test_nonlocal_rejects_weak_secret(environment, secret):
    with pytest.raises(ValidationError, match="secret"):
        Settings(_env_file=None, environment=environment, secret_key=secret)


@pytest.mark.parametrize("environment", ["local", "development", "test"])
def test_local_accepts_demo_secret(environment):
    assert Settings(_env_file=None, environment=environment, secret_key="change-me").secret_key == "change-me"


def test_production_accepts_strong_secret():
    assert Settings(_env_file=None, environment="production", secret_key="x" * 32, allow_mock_providers=True)
