import smtplib

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.notifications.channels import (
    ConsoleChannel,
    Message,
    PermanentNotification,
    SmtpChannel,
    TransientNotification,
    UnknownNotification,
    get_channel,
)


@pytest.mark.parametrize(
    "stage,exception,expected",
    [
        ("connect", OSError("private"), TransientNotification),
        ("send", smtplib.SMTPServerDisconnected("private"), UnknownNotification),
        ("send", smtplib.SMTPDataError(450, b"private"), TransientNotification),
        ("send", smtplib.SMTPDataError(550, b"private"), PermanentNotification),
        ("send", smtplib.SMTPRecipientsRefused({"private@example.com": (450, b"private")}), TransientNotification),
        ("send", smtplib.SMTPRecipientsRefused({"private@example.com": (550, b"private")}), PermanentNotification),
        ("send", smtplib.SMTPException("private"), PermanentNotification),
    ],
)
def test_smtp_error_classification_is_safe(monkeypatch, stage, exception, expected):
    class SMTP:
        def __init__(self, *a, **kw):
            if stage == "connect":
                raise exception

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

        def ehlo(self):
            pass

        def send_message(self, message):
            raise exception

    monkeypatch.setattr(smtplib, "SMTP", SMTP)
    with pytest.raises(expected) as failure:
        SmtpChannel(Settings(_env_file=None)).send(
            Message("id", "order_confirmed", "private@example.com", "Subject", "Private body", "<p>Private</p>")
        )
    assert "private" not in str(failure.value)


def test_smtp_tls_auth_success_and_quit_failure_do_not_resend(monkeypatch, caplog):
    calls = []

    class SMTP:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            raise smtplib.SMTPResponseException(421, b"private quit")

        def ehlo(self):
            calls.append("ehlo")

        def starttls(self, **kw):
            calls.append("tls")

        def login(self, *a):
            calls.append("auth")

        def send_message(self, message):
            assert message.get_body(preferencelist=("html",)) is not None
            calls.append("send")
            return {}

    monkeypatch.setattr(smtplib, "SMTP", SMTP)
    settings = Settings(
        _env_file=None, smtp_use_tls=True, smtp_username="user", smtp_password=SecretStr("private-test-pass")
    )
    message = Message("id", "order_confirmed", "private@example.com", "Subject", "Private body", "<p>Private</p>")
    get_channel("smtp", settings).send(message)
    assert calls == ["ehlo", "tls", "ehlo", "auth", "send"]
    ConsoleChannel().send(message)
    assert "private@example.com" not in caplog.text and "Private body" not in caplog.text
    assert isinstance(get_channel("console", settings), ConsoleChannel)
    with pytest.raises(PermanentNotification):
        get_channel("unknown", settings)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, notifications_backend="unknown")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, smtp_from="bad")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, notification_lease_seconds=1)
