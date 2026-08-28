"""
mailer.py — SMTP email sending (Gmail-friendly) with a DB send-log.

Configuration (put these in .env — NEVER commit real credentials):

    SMTP_HOST=smtp.gmail.com
    SMTP_PORT=587
    SMTP_USER=youraddress@gmail.com
    SMTP_PASSWORD=your_16_char_app_password   # Gmail App Password (needs 2FA)
    SMTP_FROM_NAME=CDAC Attendance
    APP_BASE_URL=https://10.212.13.129:8000   # used for links in emails

Gmail note: a normal account password will NOT work. Create an App Password at
https://myaccount.google.com/apppasswords (requires 2-Step Verification).

Nothing here sends mail on import; sending is always an explicit call.
"""

import os
import smtplib
import logging
from email.message import EmailMessage
from email.utils import formataddr
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)


def _cfg():
    return {
        "host": os.getenv("SMTP_HOST", "smtp.gmail.com").strip(),
        "port": int(os.getenv("SMTP_PORT", "587") or 587),
        "user": os.getenv("SMTP_USER", "").strip(),
        "password": os.getenv("SMTP_PASSWORD", "").strip(),
        "from_name": os.getenv("SMTP_FROM_NAME", "CDAC Attendance").strip(),
    }


def is_configured() -> bool:
    """True when SMTP credentials are present."""
    c = _cfg()
    return bool(c["user"] and c["password"])


def base_url() -> str:
    return os.getenv("APP_BASE_URL", "").strip().rstrip("/")


def _log_send(to_email, subject, kind, status, error=None):
    """Record the attempt in the email_log table (best-effort)."""
    try:
        from db import get_connection
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO email_log (to_email, subject, kind, status, error, sent_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (to_email, subject, kind, status, error, datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception as e:                                  # table may not exist yet
        logger.debug(f"email_log write skipped: {e}")


def send_email(to_email: str, subject: str, html_body: str,
               text_body: str = None, kind: str = "generic"):
    """Send one email. Returns (ok: bool, message: str). Never raises."""
    if not to_email or "@" not in to_email:
        return False, "Invalid recipient address"

    if not is_configured():
        msg = "SMTP not configured (set SMTP_USER and SMTP_PASSWORD in .env)"
        _log_send(to_email, subject, kind, "skipped", msg)
        return False, msg

    c = _cfg()
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((c["from_name"], c["user"]))
    msg["To"] = to_email
    msg.set_content(text_body or _html_to_text(html_body))
    msg.add_alternative(html_body, subtype="html")

    try:
        with smtplib.SMTP(c["host"], c["port"], timeout=30) as server:
            server.starttls()
            server.login(c["user"], c["password"])
            server.send_message(msg)
        _log_send(to_email, subject, kind, "sent")
        return True, "sent"
    except smtplib.SMTPAuthenticationError:
        err = "SMTP auth failed — for Gmail use an App Password, not your account password"
        _log_send(to_email, subject, kind, "failed", err)
        return False, err
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        _log_send(to_email, subject, kind, "failed", err)
        return False, err


def _html_to_text(html: str) -> str:
    """Very small HTML -> text fallback for the plain-text part."""
    import re
    text = re.sub(r"<br\s*/?>|</p>|</h[1-6]>|</tr>", "\n", html or "")
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ---------------------------------------------------------------------------
# Shared HTML shell so every email looks consistent
# ---------------------------------------------------------------------------

def render_email(title: str, intro: str, body_html: str = "", footer: str = None) -> str:
    """Wrap content in a simple, email-client-safe layout (inline styles only)."""
    foot = footer or "This is an automated message from the CDAC Attendance system."
    return f"""\
<div style="margin:0;padding:24px;background:#F4F5F7;font-family:Arial,Helvetica,sans-serif;">
  <div style="max-width:600px;margin:0 auto;background:#ffffff;border:1px solid #DFE1E6;border-radius:8px;overflow:hidden;">
    <div style="background:#0052CC;padding:20px 24px;">
      <h1 style="margin:0;color:#ffffff;font-size:18px;">CDAC Attendance</h1>
    </div>
    <div style="padding:24px;color:#172B4D;font-size:14px;line-height:1.6;">
      <h2 style="margin:0 0 12px;font-size:16px;color:#172B4D;">{title}</h2>
      <p style="margin:0 0 16px;">{intro}</p>
      {body_html}
    </div>
    <div style="padding:16px 24px;background:#FAFBFC;border-top:1px solid #DFE1E6;color:#6B778C;font-size:12px;">
      {foot}
    </div>
  </div>
</div>"""


def stat_table(rows) -> str:
    """rows: list of (label, value) -> a simple 2-column table."""
    trs = "".join(
        f'<tr><td style="padding:8px 12px;border-bottom:1px solid #DFE1E6;color:#6B778C;">{k}</td>'
        f'<td style="padding:8px 12px;border-bottom:1px solid #DFE1E6;font-weight:bold;text-align:right;">{v}</td></tr>'
        for k, v in rows
    )
    return f'<table style="width:100%;border-collapse:collapse;margin:8px 0 16px;">{trs}</table>'
