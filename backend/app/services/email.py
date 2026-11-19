from __future__ import annotations

import email.utils
import logging
import smtplib
import uuid
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors import crypto
from app.core.config import get_settings
from app.db.models.email_setting import EmailSetting

logger = logging.getLogger("opsmind.email")

@dataclass
class EmailConfig:
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    from_email: str = "noreply@opsmind.internal"
    from_name: str = "OpsMind"
    use_tls: bool = True
    use_ssl: bool = False
    is_active: bool = True

    @property
    def is_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_host.strip())

_SMTP_SECRET_PREFIX = "enc:v1:"
_SMTP_SECRET_CONTEXT = "email_settings"


def _keyring() -> crypto.KeyRing:
    settings = get_settings()
    return crypto.KeyRing.from_env(
        {
            "CONNECTOR_ENCRYPTION_KEYS": settings.connector_encryption_keys,
            "CONNECTOR_ENCRYPTION_CURRENT_KEY_ID": settings.connector_encryption_current_key_id,
        }
    )


def encrypt_smtp_password(plain: str, tenant_id: uuid.UUID) -> str:
    ciphertext, key_id = crypto.encrypt(
        plain, tenant_id=tenant_id, connection_id=_SMTP_SECRET_CONTEXT, keyring=_keyring()
    )
    return f"{_SMTP_SECRET_PREFIX}{key_id}:{ciphertext}"


def decrypt_smtp_password(stored: str | None, tenant_id: uuid.UUID) -> str:
    if not stored:
        return ""
    if not stored.startswith(_SMTP_SECRET_PREFIX):
        return stored
    key_id, _, ciphertext = stored[len(_SMTP_SECRET_PREFIX):].partition(":")
    return crypto.decrypt_bytes(
        ciphertext,
        key_id,
        tenant_id=tenant_id,
        connection_id=_SMTP_SECRET_CONTEXT,
        keyring=_keyring(),
    ).decode()


def _system_email_config() -> EmailConfig:
    settings = get_settings()
    return EmailConfig(
        smtp_host=settings.smtp_host,
        smtp_port=settings.smtp_port,
        smtp_username=settings.smtp_username,
        smtp_password=settings.smtp_password,
        from_email=settings.smtp_from_email,
        from_name=settings.smtp_from_name,
        use_tls=settings.smtp_use_tls,
        use_ssl=settings.smtp_use_ssl,
        is_active=True,
    )


def get_effective_email_config(
    db: Session | None = None, tenant_id: uuid.UUID | None = None
) -> EmailConfig:
    """Tenant SMTP for that tenant only; otherwise the system SMTP from environment settings."""
    if db is None or tenant_id is None:
        return _system_email_config()
    try:
        setting = db.scalar(
            select(EmailSetting)
            .where(EmailSetting.is_active.is_(True), EmailSetting.tenant_id == tenant_id)
            .order_by(EmailSetting.updated_at.desc())
        )
        if setting and setting.smtp_host:
            return EmailConfig(
                smtp_host=setting.smtp_host,
                smtp_port=setting.smtp_port,
                smtp_username=setting.smtp_username or "",
                smtp_password=decrypt_smtp_password(setting.smtp_password, tenant_id),
                from_email=setting.from_email or "noreply@opsmind.internal",
                from_name=setting.from_name or "OpsMind",
                use_tls=setting.use_tls,
                use_ssl=setting.use_ssl,
                is_active=setting.is_active,
            )
    except Exception as exc:
        logger.warning("Failed to read tenant email settings, using system SMTP: %s", exc)
    return _system_email_config()


def send_email(
    to_email: str,
    subject: str,
    html_content: str,
    text_content: str | None = None,
    config: EmailConfig | None = None,
) -> tuple[bool, str]:
    """Sends an email using the specified EmailConfig or system defaults.
    
    If SMTP is not configured, logs the message and returns success (development mode).
    """
    if config is None:
        config = get_effective_email_config()

    if not config.is_configured:
        logger.info(
            "[EMAIL SIMULATION] SMTP not configured. To: %s | Subject: '%s' | From: '%s <%s>'",
            to_email,
            subject,
            config.from_name,
            config.from_email,
        )
        return True, "Email delivery simulated (SMTP server not configured)."

    sender_email = config.from_email
    if "gmail" in config.smtp_host.lower() and config.smtp_username and "@" in config.smtp_username:
        if not sender_email or sender_email.endswith(".internal") or "@opsmind" in sender_email:
            sender_email = config.smtp_username

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = email.utils.formataddr((config.from_name, sender_email))
    msg["To"] = to_email
    msg["Date"] = email.utils.formatdate(localtime=True)

    if text_content:
        msg.attach(MIMEText(text_content, "plain", "utf-8"))
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    try:
        if config.use_ssl:
            server = smtplib.SMTP_SSL(config.smtp_host, config.smtp_port, timeout=12)
        else:
            server = smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=12)

        with server:
            if config.use_tls and not config.use_ssl:
                server.starttls()
            if config.smtp_username and config.smtp_password:
                server.login(config.smtp_username, config.smtp_password)
            server.sendmail(sender_email, [to_email], msg.as_string())

        logger.info("Email sent successfully to %s: '%s'", to_email, subject)
        return True, "Email sent successfully."
    except smtplib.SMTPAuthenticationError as exc:
        err = f"SMTP Authentication failed: {exc.smtp_error.decode() if isinstance(exc.smtp_error, bytes) else str(exc)}"
        if "gmail" in config.smtp_host.lower():
            err += ". Note for Gmail: You must use a 16-character Google App Password (myaccount.google.com/apppasswords), not your regular Google account password."
        logger.error(err)
        return False, err
    except smtplib.SMTPServerDisconnected:
        if "gmail" in config.smtp_host.lower() and ("@" not in config.smtp_username or " " in config.smtp_username):
            err = "Gmail SMTP disconnected: 'Username / Account Email' must be your full Gmail address (e.g. yourname@gmail.com), not a display name like 'OpsMind SMTP'."
        else:
            err = f"Failed to send email via SMTP: Connection unexpectedly closed by {config.smtp_host}. Check that your username is an email address, your password/App Password is valid, or try switching between port 587 (TLS) and port 465 (SSL)."
        logger.error(err)
        return False, err
    except Exception as exc:
        err = f"Failed to send email via SMTP: {str(exc)}"
        logger.error(err)
        return False, err

def send_invitation_email(
    to_email: str,
    tenant_name: str,
    role: str,
    token: str,
    db: Session | None = None,
    tenant_id: uuid.UUID | None = None,
) -> tuple[bool, str]:
    """Dispatches a branded invitation email to the invitee."""
    settings = get_settings()
    accept_url = f"{settings.frontend_url.rstrip('/')}/accept-invite?token={token}"
    config = get_effective_email_config(db, tenant_id)

    subject = f"You have been invited to join {tenant_name} on OpsMind"
    html_content = f"""
    <!DOCTYPE html>
    <html>
      <head>
        <meta charset="utf-8">
        <style>
          body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; margin: 0; padding: 24px; color: #1e293b; }}
          .card {{ max-width: 540px; margin: 0 auto; background: #ffffff; border-radius: 16px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); }}
          .header {{ background: #0f172a; padding: 28px 32px; text-align: center; color: #ffffff; }}
          .body {{ padding: 32px; }}
          .badge {{ display: inline-block; background: #e0e7ff; color: #4338ca; padding: 4px 12px; border-radius: 9999px; font-size: 12px; font-weight: 600; text-transform: uppercase; }}
          .btn {{ display: inline-block; background: #4f46e5; color: #ffffff !important; text-decoration: none; padding: 12px 28px; border-radius: 10px; font-weight: 600; font-size: 14px; margin: 20px 0; }}
          .footer {{ padding: 20px 32px; background: #f8fafc; border-top: 1px solid #e2e8f0; font-size: 11px; color: #64748b; text-align: center; }}
        </style>
      </head>
      <body>
        <div class="card">
          <div class="header">
            <h2 style="margin: 0; font-size: 20px;">OpsMind</h2>
            <p style="margin: 4px 0 0 0; font-size: 12px; color: #94a3b8;">AI-Native Operations Platform</p>
          </div>
          <div class="body">
            <h3 style="margin-top: 0; font-size: 18px; color: #0f172a;">You've been invited!</h3>
            <p style="font-size: 14px; line-height: 1.6; color: #475569;">
              You have been invited to join <strong>{tenant_name}</strong> on OpsMind as an 
              <span class="badge">{role}</span>.
            </p>
            <p style="font-size: 13px; color: #64748b;">
              Click the button below to accept your invitation and join the workspace:
            </p>
            <div style="text-align: center;">
              <a href="{accept_url}" class="btn">Accept Invitation</a>
            </div>
            <p style="font-size: 11px; color: #94a3b8; word-break: break-all;">
              Or paste this URL in your browser: <br>
              <a href="{accept_url}" style="color: #4f46e5;">{accept_url}</a>
            </p>
          </div>
          <div class="footer">
            This invitation link is valid for 7 days. If you did not expect this invitation, you can ignore this email.
          </div>
        </div>
      </body>
    </html>
    """

    text_content = f"You have been invited to join {tenant_name} on OpsMind as {role}.\nAccept your invite here: {accept_url}\nValid for 7 days."
    return send_email(to_email, subject, html_content, text_content, config)

def send_password_reset_email(
    to_email: str,
    reset_url: str,
    db: Session | None = None,
    tenant_id: uuid.UUID | None = None,
) -> tuple[bool, str]:
    """Dispatches a branded password reset email via configured outgoing SMTP."""
    config = get_effective_email_config(db, tenant_id)
    subject = "Reset your OpsMind password"
    html_content = f"""
    <!DOCTYPE html>
    <html>
      <head>
        <meta charset="utf-8">
        <style>
          body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; margin: 0; padding: 24px; color: #1e293b; }}
          .card {{ max-width: 540px; margin: 0 auto; background: #ffffff; border-radius: 16px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); }}
          .header {{ background: #0f172a; padding: 28px 32px; text-align: center; color: #ffffff; }}
          .body {{ padding: 32px; }}
          .btn {{ display: inline-block; background: #4f46e5; color: #ffffff !important; text-decoration: none; padding: 12px 28px; border-radius: 10px; font-weight: 600; font-size: 14px; margin: 20px 0; }}
          .footer {{ padding: 20px 32px; background: #f8fafc; border-top: 1px solid #e2e8f0; font-size: 11px; color: #64748b; text-align: center; }}
        </style>
      </head>
      <body>
        <div class="card">
          <div class="header">
            <h2 style="margin: 0; font-size: 20px;">OpsMind</h2>
            <p style="margin: 4px 0 0 0; font-size: 12px; color: #94a3b8;">AI-Native Operations Platform</p>
          </div>
          <div class="body">
            <h3 style="margin-top: 0; font-size: 18px; color: #0f172a;">Password Reset Request</h3>
            <p style="font-size: 14px; line-height: 1.6; color: #475569;">
              We received a request to reset the password for your OpsMind account (<strong>{to_email}</strong>).
            </p>
            <p style="font-size: 13px; color: #64748b;">
              Click the button below to choose a new password:
            </p>
            <div style="text-align: center;">
              <a href="{reset_url}" class="btn">Reset Password</a>
            </div>
            <p style="font-size: 11px; color: #94a3b8; word-break: break-all;">
              Or copy and paste this URL into your browser: <br>
              <a href="{reset_url}" style="color: #4f46e5;">{reset_url}</a>
            </p>
          </div>
          <div class="footer">
            This password reset link is valid for 15 minutes. If you did not make this request, you can safely ignore this email.
          </div>
        </div>
      </body>
    </html>
    """

    text_content = (
        f"OpsMind Password Reset Request\n\n"
        f"Reset your password using the following link:\n{reset_url}\n\n"
        f"This link expires in 15 minutes. If you did not request this, you can ignore this email."
    )
    return send_email(to_email, subject, html_content, text_content, config)

