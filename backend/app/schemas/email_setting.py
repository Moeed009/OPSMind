import uuid

from pydantic import BaseModel, ConfigDict, Field


class EmailSettingOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    provider: str
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    has_password: bool = False
    from_email: str
    from_name: str
    use_tls: bool = True
    use_ssl: bool = False
    is_active: bool = True

class EmailSettingUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provider: str = "smtp"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    from_email: str = "noreply@opsmind.internal"
    from_name: str = "OpsMind"
    use_tls: bool = True
    use_ssl: bool = False
    is_active: bool = True

class TestEmailRequest(BaseModel):
    recipient_email: str = Field(min_length=5, max_length=320)

class TestEmailResponse(BaseModel):
    success: bool
    message: str
