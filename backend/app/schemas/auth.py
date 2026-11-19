from pydantic import BaseModel, EmailStr, Field


class SignupRequest(BaseModel):
    email: EmailStr | None = None
    password: str | None = Field(default=None, max_length=128)
    full_name: str | None = Field(default=None, max_length=200)
    encrypted: str | None = None

class LoginRequest(BaseModel):
    email: EmailStr | None = None
    password: str | None = Field(default=None, max_length=128)
    encrypted: str | None = None

class PublicKeyResponse(BaseModel):
    public_key: str

class PasswordResetRequest(BaseModel):
    email: EmailStr

class PasswordResetConfirmRequest(BaseModel):
    access_token: str = Field(min_length=20)
    new_password: str = Field(min_length=12, max_length=128)

class AuthUserResponse(BaseModel):
    id: str
    email: EmailStr
    full_name: str | None = None

class AuthSessionResponse(BaseModel):
    authenticated: bool = True
    user: AuthUserResponse
    expires_in: int | None = None
    tenant_id: str | None = None

class MessageResponse(BaseModel):
    message: str

class SignupResponse(BaseModel):
    message: str
    confirmation_required: bool
    user: AuthUserResponse

class OAuthUrlResponse(BaseModel):
    url: str

class OAuthSessionRequest(BaseModel):
    access_token: str | None = None
    refresh_token: str | None = None
    code: str | None = None
