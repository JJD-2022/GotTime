from dataclasses import dataclass
from typing import Optional


@dataclass
class Message:
    subject: str = ""
    recipients: Optional[list] = None
    body: str = ""
    html: str = ""
    sender: Optional[str] = None

    def __post_init__(self):
        if not self.recipients:
            raise ValueError("At least one recipient is required")


class Mail:
    def __init__(self, app=None):
        self.app = app
        if app is not None:
            self.init_app(app)

    def init_app(self, app):
        self.app = app

    def send(self, message):
        if self.app:
            self.app.logger.info(
                "Email would be sent to %s with subject %s",
                message.recipients,
                message.subject,
            )
        return True

    def send_message(self, message):
        return self.send(message)
