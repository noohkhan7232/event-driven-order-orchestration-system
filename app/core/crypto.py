"""Authenticated credential storage; comma-separated keys support staged rotation."""

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from pydantic import SecretStr

from app.core.exceptions import DomainError


class CredentialsUnavailable(DomainError):
    status_code = 503
    code = "credentials_unavailable"

    def __init__(self) -> None:
        super().__init__("Payment credentials unavailable")


def credential_ring(key: SecretStr) -> MultiFernet:
    return MultiFernet([Fernet(part.strip().encode("ascii")) for part in key.get_secret_value().split(",")])


class CredentialVault:
    def __init__(self, key: SecretStr | None) -> None:
        self._ring = credential_ring(key) if key else None

    def encrypt(self, value: SecretStr) -> str:
        if self._ring is None:
            raise CredentialsUnavailable()
        return self._ring.encrypt(value.get_secret_value().encode()).decode("ascii")

    def decrypt(self, value: str) -> SecretStr:
        if self._ring is None:
            raise CredentialsUnavailable()
        try:
            return SecretStr(self._ring.decrypt(value.encode("ascii")).decode())
        except (InvalidToken, ValueError, UnicodeError):
            raise CredentialsUnavailable() from None

    def rotate(self, value: str) -> str:
        if self._ring is None:
            raise CredentialsUnavailable()
        try:
            return self._ring.rotate(value.encode("ascii")).decode("ascii")
        except (InvalidToken, ValueError, UnicodeError):
            raise CredentialsUnavailable() from None
