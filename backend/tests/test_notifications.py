from __future__ import annotations

import pytest

from backend.app import database, notifications


class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, host: str, port: int, timeout: int):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.login_args: tuple[str, str] | None = None
        self.message = None
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def starttls(self):
        self.started_tls = True

    def login(self, username: str, password: str):
        self.login_args = (username, password)

    def send_message(self, message):
        self.message = message


@pytest.fixture(autouse=True)
def reset_fake_smtp():
    FakeSMTP.instances.clear()


def test_parse_recipients_accepts_commas_and_semicolons():
    assert notifications.parse_recipients("a@example.com; b@example.com, c@example.com") == [
        "a@example.com",
        "b@example.com",
        "c@example.com",
    ]


def test_send_mail_uses_starttls_and_login(monkeypatch):
    monkeypatch.setattr(notifications.smtplib, "SMTP", FakeSMTP)
    settings = {
        "smtp_host": "smtp.example.com",
        "smtp_port": "587",
        "smtp_username": "user@example.com",
        "smtp_password": "secret",
        "from_address": "noreply@example.com",
        "to_addresses": "ops@example.com,dev@example.com",
        "smtp_security": "tls",
    }

    notifications.send_mail(settings, "subject", "body")

    smtp = FakeSMTP.instances[0]
    assert smtp.host == "smtp.example.com"
    assert smtp.port == 587
    assert smtp.started_tls is True
    assert smtp.login_args == ("user@example.com", "secret")
    assert smtp.message["Subject"] == "subject"
    assert smtp.message["To"] == "ops@example.com, dev@example.com"
    assert smtp.message.get_content() == "body\n"


def test_task_notification_skips_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.sqlite")
    database.init_db()
    task_id = database.create_task("https://www.youtube.com/watch?v=maildisabled")
    database.update_task(task_id, status="succeeded", completed_at=database.now_iso())

    def fail_send(*args, **kwargs):
        raise AssertionError("disabled notification should not send")

    monkeypatch.setattr(notifications, "send_mail", fail_send)

    assert notifications.send_task_notification(database.get_task(task_id), "succeeded") == "skipped: disabled"


def test_task_notification_sends_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.sqlite")
    database.init_db()
    database.save_mail_settings(
        enabled="true",
        smtp_host="smtp.example.com",
        smtp_port="587",
        smtp_username="user@example.com",
        smtp_password="secret",
        from_address="noreply@example.com",
        to_addresses="ops@example.com",
        smtp_security="tls",
        notify_on_success="true",
        notify_on_failure="true",
    )
    task_id = database.create_task("https://www.youtube.com/watch?v=mailsendxxx", task_id="mailsendxxx")
    database.update_task(task_id, status="succeeded", title="Demo", completed_at=database.now_iso())
    sent: list[tuple[str, str]] = []

    def capture_send(settings, subject, body):
        sent.append((subject, body))

    monkeypatch.setattr(notifications, "send_mail", capture_send)

    assert notifications.send_task_notification(database.get_task(task_id), "succeeded") == "sent"
    assert sent
    assert "YouDub 任务完成: Demo" == sent[0][0]
    assert "任务 ID: mailsendxxx" in sent[0][1]
