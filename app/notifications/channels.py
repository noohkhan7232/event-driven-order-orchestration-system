import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Protocol

from app.core.config import Settings
from app.core.logging import get_logger


class TransientNotification(Exception):
    pass


class PermanentNotification(Exception):
    pass


class UnknownNotification(Exception):
    pass


@dataclass(frozen=True)
class Message:
    identity: str
    template: str
    recipient: str = field(repr=False)
    subject: str = field(repr=False)
    text: str = field(repr=False)
    html: str = field(repr=False)


class NotificationChannel(Protocol):
    def send(self, message: Message) -> None: ...


class ConsoleChannel:
    def send(self, message: Message) -> None:
        get_logger(__name__).info("notification_console", notification_id=message.identity, template=message.template)


class SmtpChannel:
    def __init__(self, settings: Settings):
        self.settings = settings

    def send(self, message: Message) -> None:
        stage = "connect"
        accepted = False
        try:
            mail = EmailMessage()
            mail["From"], mail["To"], mail["Subject"] = self.settings.smtp_from, message.recipient, message.subject
            mail["Message-ID"] = f"<{message.identity}@oos.notifications>"
            mail.set_content(message.text)
            mail.add_alternative(message.html, subtype="html")
            with smtplib.SMTP(
                self.settings.smtp_host, self.settings.smtp_port, timeout=self.settings.smtp_timeout_seconds
            ) as client:
                client.ehlo()
                if self.settings.smtp_use_tls:
                    import ssl

                    client.starttls(context=ssl.create_default_context())
                    client.ehlo()
                if self.settings.smtp_username:
                    client.login(
                        self.settings.smtp_username,
                        self.settings.smtp_password.get_secret_value() if self.settings.smtp_password else "",
                    )
                stage = "send"
                refused = client.send_message(mail)
                if refused:
                    raise PermanentNotification()
                accepted = True
        except smtplib.SMTPRecipientsRefused as exc:
            if accepted:
                return
            if exc.recipients and all(400 <= value[0] < 500 for value in exc.recipients.values()):
                raise TransientNotification() from None
            raise PermanentNotification() from None
        except smtplib.SMTPResponseException as exc:
            if accepted:
                return
            raise (TransientNotification() if 400 <= exc.smtp_code < 500 else PermanentNotification()) from None
        except smtplib.SMTPServerDisconnected:
            if accepted:
                return
            raise (UnknownNotification() if stage == "send" else TransientNotification()) from None
        except (smtplib.SMTPException, ValueError):
            if accepted:
                return
            raise PermanentNotification() from None
        except OSError:
            if accepted:
                return
            raise (UnknownNotification() if stage == "send" else TransientNotification()) from None


def get_channel(name: str, settings: Settings) -> NotificationChannel:
    if name == "console":
        return ConsoleChannel()
    if name == "smtp":
        return SmtpChannel(settings)
    raise PermanentNotification()
