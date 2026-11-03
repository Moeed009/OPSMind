from pydantic import BaseModel, EmailStr, Field


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    full_name: str | None = Field(default=None, min_length=1, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


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


class MessageResponse(BaseModel):
    message: str


class PasswordResetRequestResponse(BaseModel):
    message: str


class SignupResponse(BaseModel):
    message: str
    confirmation_required: bool
    user: AuthUserResponse
