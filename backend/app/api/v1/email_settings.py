import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.api.deps import TenantContext
from app.api.rbac import require_permission
from app.core.permissions import Permission
from app.db.models.email_setting import EmailSetting
from app.schemas.email_setting import (
    EmailSettingOut,
    EmailSettingUpdate,
    TestEmailRequest,
    TestEmailResponse,
)
from app.services import email as email_service

router = APIRouter(prefix="/settings/email", tags=["Email Settings"])

AdminContext = Annotated[
    TenantContext, Depends(require_permission(Permission.CONNECTIONS_MANAGE))
]

@router.get("", response_model=EmailSettingOut)
def get_email_settings(tc: AdminContext) -> EmailSettingOut:
    db = tc.db
    setting = db.scalar(select(EmailSetting).where(EmailSetting.tenant_id == tc.tenant_id))

    if not setting:
        sys_config = email_service.get_effective_email_config()
        return EmailSettingOut(
            id=uuid.uuid4(),
            tenant_id=tc.tenant_id,
            provider="smtp",
            smtp_host=sys_config.smtp_host or None,
            smtp_port=sys_config.smtp_port,
            smtp_username=sys_config.smtp_username or None,
            has_password=bool(sys_config.smtp_password),
            from_email=sys_config.from_email,
            from_name=sys_config.from_name,
            use_tls=sys_config.use_tls,
            use_ssl=sys_config.use_ssl,
            is_active=True,
        )

    return EmailSettingOut(
        id=setting.id,
        tenant_id=setting.tenant_id,
        provider=setting.provider,
        smtp_host=setting.smtp_host,
        smtp_port=setting.smtp_port,
        smtp_username=setting.smtp_username,
        has_password=bool(setting.smtp_password),
        from_email=setting.from_email,
        from_name=setting.from_name,
        use_tls=setting.use_tls,
        use_ssl=setting.use_ssl,
        is_active=setting.is_active,
    )

def _encrypted_password(plain: str | None, tenant_id: uuid.UUID) -> str | None:
    if not plain:
        return None
    try:
        return email_service.encrypt_smtp_password(plain, tenant_id)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Credential encryption is not configured on the server",
        ) from exc


@router.put("", response_model=EmailSettingOut)
def update_email_settings(payload: EmailSettingUpdate, tc: AdminContext) -> EmailSettingOut:
    db = tc.db
    setting = db.scalar(select(EmailSetting).where(EmailSetting.tenant_id == tc.tenant_id))
    encrypted_password = _encrypted_password(payload.smtp_password, tc.tenant_id)

    if not setting:
        setting = EmailSetting(
            tenant_id=tc.tenant_id,
            provider=payload.provider,
            smtp_host=payload.smtp_host,
            smtp_port=payload.smtp_port,
            smtp_username=payload.smtp_username,
            smtp_password=encrypted_password,
            from_email=payload.from_email,
            from_name=payload.from_name,
            use_tls=payload.use_tls,
            use_ssl=payload.use_ssl,
            is_active=payload.is_active,
        )
        db.add(setting)
    else:
        setting.provider = payload.provider
        setting.smtp_host = payload.smtp_host
        setting.smtp_port = payload.smtp_port
        setting.smtp_username = payload.smtp_username
        if encrypted_password:
            setting.smtp_password = encrypted_password
        setting.from_email = payload.from_email
        setting.from_name = payload.from_name
        setting.use_tls = payload.use_tls
        setting.use_ssl = payload.use_ssl
        setting.is_active = payload.is_active

    db.flush()

    return EmailSettingOut(
        id=setting.id,
        tenant_id=setting.tenant_id,
        provider=setting.provider,
        smtp_host=setting.smtp_host,
        smtp_port=setting.smtp_port,
        smtp_username=setting.smtp_username,
        has_password=bool(setting.smtp_password),
        from_email=setting.from_email,
        from_name=setting.from_name,
        use_tls=setting.use_tls,
        use_ssl=setting.use_ssl,
        is_active=setting.is_active,
    )

@router.post("/test", response_model=TestEmailResponse)
def send_test_email(payload: TestEmailRequest, tc: AdminContext) -> TestEmailResponse:
    config = email_service.get_effective_email_config(tc.db, tc.tenant_id)
    subject = "OpsMind SMTP Email Test"
    html_content = f"""
    <!DOCTYPE html>
    <html>
      <body style="font-family: sans-serif; padding: 24px; color: #1e293b;">
        <div style="max-width: 500px; margin: 0 auto; border: 1px solid #e2e8f0; border-radius: 12px; padding: 24px;">
          <h2 style="color: #4f46e5; margin-top: 0;">OpsMind SMTP Connection Verified!</h2>
          <p style="font-size: 14px; color: #475569;">
            This is a test email sent from your OpsMind workspace to verify that your outgoing SMTP email configuration is active and working properly.
          </p>
          <div style="background: #f1f5f9; padding: 12px; border-radius: 8px; font-size: 12px; color: #334155;">
            <strong>SMTP Host:</strong> {config.smtp_host or 'System Default'}<br>
            <strong>SMTP Port:</strong> {config.smtp_port}<br>
            <strong>From:</strong> {config.from_name} &lt;{config.from_email}&gt;<br>
            <strong>TLS Enabled:</strong> {config.use_tls}
          </div>
          <p style="font-size: 11px; color: #94a3b8; margin-top: 20px;">
            Sent automatically by OpsMind Operations Engine.
          </p>
        </div>
      </body>
    </html>
    """
    text_content = "OpsMind SMTP Email Test - Outgoing email connection verified successfully!"

    success, message = email_service.send_email(
        to_email=payload.recipient_email,
        subject=subject,
        html_content=html_content,
        text_content=text_content,
        config=config,
    )

    if not success:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, message)

    return TestEmailResponse(success=True, message=message)
