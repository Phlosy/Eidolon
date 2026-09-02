"""Verification-email delivery with an explicit console or SMTP adapter."""

import smtplib
from email.message import EmailMessage
from urllib.parse import urlencode

from app.core.config import settings


def send_verification_email(email: str, token: str, *, locale: str) -> None:
    if settings.email_delivery_mode == "console":
        return
    if settings.email_delivery_mode != "smtp":
        raise RuntimeError("EIDOLON_EMAIL_DELIVERY_MODE must be console or smtp")
    if not settings.smtp_host or not settings.smtp_from:
        raise RuntimeError("SMTP delivery requires EIDOLON_SMTP_HOST and EIDOLON_SMTP_FROM")

    query = urlencode({"email": email, "token": token})
    link = f"{settings.web_app_url.rstrip('/')}/auth/verify?{query}"
    chinese = locale.startswith("zh")
    message = EmailMessage()
    message["Subject"] = "验证你的 Eidolon 账号" if chinese else "Verify your Eidolon account"
    message["From"] = settings.smtp_from
    message["To"] = email
    if chinese:
        body = f"请在 {settings.email_verification_ttl_minutes} 分钟内打开以下链接：\n\n{link}\n"
    else:
        body = (
            f"Open this link within {settings.email_verification_ttl_minutes} minutes:\n\n{link}\n"
        )
    message.set_content(body)

    client_type = smtplib.SMTP_SSL if settings.smtp_use_ssl else smtplib.SMTP
    with client_type(settings.smtp_host, settings.smtp_port, timeout=10) as client:
        if settings.smtp_starttls and not settings.smtp_use_ssl:
            client.starttls()
        if settings.smtp_username:
            client.login(settings.smtp_username, settings.smtp_password or "")
        client.send_message(message)
