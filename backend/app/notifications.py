from __future__ import annotations

import smtplib
from email.message import EmailMessage
from email.utils import formatdate

from . import database

SMTP_SECURITY_MODES = {"none", "tls", "ssl"}
TRUE_VALUES = {"1", "true", "yes", "on"}


def setting_is_enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in TRUE_VALUES


def parse_recipients(value: str) -> list[str]:
    recipients: list[str] = []
    for part in value.replace(";", ",").split(","):
        recipient = part.strip()
        if recipient:
            recipients.append(recipient)
    return recipients


def _task_subject(task: dict, event: str) -> str:
    title = (task.get("title") or task.get("id") or "Untitled task").strip()
    prefix = "YouDub 任务完成" if event == "succeeded" else "YouDub 任务失败"
    return f"{prefix}: {title}"


def _task_body(task: dict, event: str) -> str:
    lines = [
        "YouDub 任务处理结果通知",
        "",
        f"任务 ID: {task.get('id') or ''}",
        f"标题: {task.get('title') or ''}",
        f"状态: {task.get('status') or event}",
        f"当前阶段: {task.get('current_stage') or ''}",
        f"URL: {task.get('url') or ''}",
        f"创建时间: {task.get('created_at') or ''}",
        f"开始时间: {task.get('started_at') or ''}",
        f"完成时间: {task.get('completed_at') or ''}",
    ]
    final_video_path = task.get("final_video_path")
    if final_video_path:
        lines.append(f"最终视频: {final_video_path}")
    session_path = task.get("session_path")
    if session_path:
        lines.append(f"会话目录: {session_path}")
    error_message = task.get("error_message")
    if error_message:
        lines.extend(["", "错误信息:", str(error_message)])
    return "\n".join(lines).strip() + "\n"


def send_mail(settings: dict[str, str], subject: str, body: str) -> None:
    host = settings["smtp_host"].strip()
    if not host:
        raise ValueError("SMTP host is required.")
    port = int(settings["smtp_port"].strip() or "587")
    security = settings["smtp_security"].strip().lower() or "tls"
    if security not in SMTP_SECURITY_MODES:
        raise ValueError("SMTP security must be one of: none, tls, ssl.")
    recipients = parse_recipients(settings["to_addresses"])
    if not recipients:
        raise ValueError("At least one recipient is required.")
    from_address = settings["from_address"].strip() or settings["smtp_username"].strip()
    if not from_address:
        raise ValueError("From address is required.")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = from_address
    message["To"] = ", ".join(recipients)
    message["Date"] = formatdate(localtime=True)
    message.set_content(body)

    if security == "ssl":
        server = smtplib.SMTP_SSL(host, port, timeout=20)
    else:
        server = smtplib.SMTP(host, port, timeout=20)
    with server:
        if security == "tls":
            server.starttls()
        username = settings["smtp_username"].strip()
        password = settings["smtp_password"].strip()
        if username or password:
            server.login(username, password)
        server.send_message(message)


def send_task_notification(task: dict, event: str) -> str:
    settings = database.get_mail_settings()
    if not setting_is_enabled(settings["enabled"]):
        return "skipped: disabled"
    if event == "succeeded" and not setting_is_enabled(settings["notify_on_success"]):
        return "skipped: success notification disabled"
    if event == "failed" and not setting_is_enabled(settings["notify_on_failure"]):
        return "skipped: failure notification disabled"

    send_mail(settings, _task_subject(task, event), _task_body(task, event))
    return "sent"


def notify_task_completion(task_id: str, event: str, log) -> None:
    task = database.get_task(task_id)
    if not task:
        return
    try:
        result = send_task_notification(task, event)
        if result == "sent":
            log("Mail notification sent")
        elif result.startswith("skipped"):
            log(f"Mail notification {result}")
    except Exception as exc:
        log(f"Mail notification failed: {exc}")
