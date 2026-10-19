from dataclasses import dataclass


@dataclass
class Message:
    subject: str
    recipients: list
    body: str = ""
    html: str = ""

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
            self.app.logger.info("Email would be sent to %s with subject %s", message.recipients, message.subject)
        return True
