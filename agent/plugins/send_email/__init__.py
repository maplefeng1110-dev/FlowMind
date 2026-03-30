import asyncio
import os
import smtplib
from email.header import Header
from email.mime.text import MIMEText

from utils.logger import setup_logger
from utils.plugin_result import error_result, success_result

logger = setup_logger("EmailPlugin", "agent.log")


def _send_email_sync(
    smtp_server: str,
    smtp_port: int,
    smtp_user: str,
    smtp_pass: str,
    to: str,
    subject: str,
    body: str,
) -> None:
    msg = MIMEText(body, "html", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = smtp_user
    msg["To"] = to

    with smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=15) as server:
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, [to], msg.as_string())


async def run(to: str, subject: str, body: str) -> dict:
    if not to or not subject or not body:
        return error_result("to, subject and body are required")

    smtp_server = os.getenv("SMTP_SERVER", "smtp.qq.com")
    smtp_port = int(os.getenv("SMTP_PORT", "465"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASSWORD")

    if not smtp_user or not smtp_pass:
        return error_result("SMTP credentials are not configured")

    try:
        await asyncio.to_thread(
            _send_email_sync,
            smtp_server,
            smtp_port,
            smtp_user,
            smtp_pass,
            to,
            subject,
            body,
        )
        logger.info("Email sent successfully to %s", to)
        return success_result(data={"to": to, "subject": subject, "sent": True})
    except Exception as exc:
        logger.error("Failed to send email: %s", exc)
        return error_result("Failed to send email", error=str(exc), data={"to": to, "sent": False})
