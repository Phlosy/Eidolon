"""Verification-email delivery with an explicit console or SMTP adapter.

邮件是 text + HTML 双格式（multipart/alternative）：支持 HTML 的客户端渲染
带品牌与按钮的版本，纯文本客户端退回纯文本。模板刻意用 table + 内联样式，
这是邮件客户端兼容性最好的写法（多数客户端不支持 flex/grid/外部 CSS）。
"""

import logging
import smtplib
from email.message import EmailMessage
from html import escape
from urllib.parse import urlencode

from app.core.config import settings

logger = logging.getLogger("eidolon.email")

_BRAND = "EIDOLON"


def _html_page(*, title: str, greeting: str, body: str, link: str, button: str, footer: str) -> str:
    font = "-apple-system,'Segoe UI',sans-serif"
    safe_link = escape(link, quote=True)
    return f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#f4f5f7;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
      style="background:#f4f5f7;padding:32px 16px;">
    <tr><td align="center">
      <table role="presentation" width="480" cellpadding="0" cellspacing="0"
          style="max-width:480px;width:100%;background:#ffffff;
                 border-radius:12px;border:1px solid #e5e7eb;">
        <tr><td style="padding:28px 32px 0;">
          <p style="margin:0;font-family:monospace;font-size:13px;
              letter-spacing:3px;color:#2563eb;font-weight:bold;">{_BRAND}</p>
          <h1 style="margin:16px 0 0;font-family:{font};font-size:20px;color:#111827;">
            {escape(title)}</h1>
        </td></tr>
        <tr><td style="padding:16px 32px 0;font-family:{font};font-size:14px;
            line-height:22px;color:#374151;">
          <p style="margin:0 0 12px;">{escape(greeting)}</p>
          <p style="margin:0;">{escape(body)}</p>
        </td></tr>
        <tr><td align="center" style="padding:24px 32px;">
          <a href="{safe_link}" style="display:inline-block;padding:12px 32px;
              background:#2563eb;color:#ffffff;font-family:{font};font-size:14px;
              font-weight:600;text-decoration:none;border-radius:10px;">{escape(button)}</a>
        </td></tr>
        <tr><td style="padding:0 32px;font-family:{font};font-size:12px;
            line-height:18px;color:#6b7280;">
          <p style="margin:0 0 8px;">{escape(footer)}</p>
          <p style="margin:0;word-break:break-all;">
            <a href="{safe_link}" style="color:#2563eb;">{escape(link)}</a></p>
        </td></tr>
        <tr><td style="padding:20px 32px 24px;font-family:{font};font-size:11px;color:#9ca3af;">
          {_BRAND} · Autonomous AI Organization Runtime
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _deliver(email: str, subject: str, text_body: str, html_body: str) -> None:
    if settings.email_delivery_mode == "console":
        # 本地开发没有邮箱：验证链接打到服务端日志，从控制台复制到浏览器打开
        logger.info("verification email to %s\nSubject: %s\n%s", email, subject, text_body)
        return
    if settings.email_delivery_mode != "smtp":
        raise RuntimeError("EIDOLON_EMAIL_DELIVERY_MODE must be console or smtp")
    if not settings.smtp_host or not settings.smtp_from:
        raise RuntimeError("SMTP delivery requires EIDOLON_SMTP_HOST and EIDOLON_SMTP_FROM")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_from
    message["To"] = email
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    client_type = smtplib.SMTP_SSL if settings.smtp_use_ssl else smtplib.SMTP
    with client_type(settings.smtp_host, settings.smtp_port, timeout=10) as client:
        if settings.smtp_starttls and not settings.smtp_use_ssl:
            client.starttls()
        if settings.smtp_username:
            client.login(settings.smtp_username, settings.smtp_password or "")
        client.send_message(message)


def send_verification_email(email: str, token: str, *, locale: str) -> None:
    query = urlencode({"email": email, "token": token})
    link = f"{settings.web_app_url.rstrip('/')}/auth/verify?{query}"
    minutes = settings.email_verification_ttl_minutes
    if locale.startswith("zh"):
        subject = "验证你的 Eidolon 账号"
        greeting = f"{email}，你好："
        body = (
            f"你正在注册 Eidolon。请在 {minutes} 分钟内点击下方按钮完成邮箱验证，"
            "验证通过后才会创建你的公司。"
        )
        button = "验证邮箱"
        footer = (
            f"按钮打不开时，复制上面的链接到浏览器。此链接 {minutes} 分钟内有效；"
            "若非本人操作请忽略。"
        )
        text = f"{greeting}\n\n{body}\n\n{link}\n\n{footer}\n"
    else:
        subject = "Verify your Eidolon account"
        greeting = f"Hello {email},"
        body = (
            f"You are registering for Eidolon. Confirm your email within {minutes} minutes "
            "using the button below — your company is created only after verification."
        )
        button = "Verify email"
        footer = (
            f"If the button does not work, copy the link above into your browser. "
            f"The link expires in {minutes} minutes. If this wasn't you, ignore this email."
        )
        text = f"{greeting}\n\n{body}\n\n{link}\n\n{footer}\n"
    html = _html_page(
        title=subject, greeting=greeting, body=body, link=link, button=button, footer=footer
    )
    _deliver(email, subject, text, html)


# 账户安全操作（改邮箱/改密码/注销）共用一套邮件确认；链接指向统一的确认页，
# action 参数让页面在执行前就能说明"这一步会做什么"。
_ACTION_COPY = {
    "change_email": {
        "zh": (
            "确认你的新邮箱",
            "你正在更换 Eidolon 的登录邮箱。点击下方按钮确认后，登录邮箱才会切换为新地址。",
        ),
        "en": (
            "Confirm your new email",
            "You are changing your Eidolon sign-in email. "
            "The change applies only after you confirm below.",
        ),
    },
    "change_password": {
        "zh": (
            "确认修改密码",
            "你正在修改 Eidolon 账号密码。点击下方按钮确认后新密码生效，所有设备都会退出登录。",
        ),
        "en": (
            "Confirm your password change",
            "You are changing your Eidolon password. "
            "Once confirmed, the new password applies and every device is signed out.",
        ),
    },
    "delete_account": {
        "zh": (
            "确认注销账号",
            "你正在注销 Eidolon 账号。点击下方按钮确认后账号立即删除，且不可恢复。",
        ),
        "en": (
            "Confirm account deletion",
            "You are deleting your Eidolon account. "
            "Once confirmed, the account is removed immediately and cannot be recovered.",
        ),
    },
}


def send_account_action_email(email: str, action: str, token: str, *, locale: str) -> None:
    query = urlencode({"email": email, "action": action, "token": token})
    link = f"{settings.web_app_url.rstrip('/')}/auth/confirm-action?{query}"
    minutes = settings.email_verification_ttl_minutes
    chinese = locale.startswith("zh")
    subject, body = _ACTION_COPY.get(action, _ACTION_COPY["change_email"])[
        "zh" if chinese else "en"
    ]
    if chinese:
        greeting = f"{email}，你好："
        button = "确认操作"
        footer = (
            f"按钮打不开时，复制上面的链接到浏览器。此链接 {minutes} 分钟内有效；"
            "若非本人操作请忽略，账号不会发生变化。"
        )
    else:
        greeting = f"Hello {email},"
        button = "Confirm"
        footer = (
            "If the button does not work, copy the link above into your browser. "
            f"The link expires in {minutes} minutes. "
            "If this wasn't you, ignore this email — nothing will change."
        )
    text = f"{greeting}\n\n{body}\n\n{link}\n\n{footer}\n"
    html = _html_page(
        title=subject, greeting=greeting, body=body, link=link, button=button, footer=footer
    )
    _deliver(email, subject, text, html)
