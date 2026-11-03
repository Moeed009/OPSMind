import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.enums import Role

EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str | None
    role: Role
    joined_at: datetime


class MemberRoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Role


class InvitationCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    email: str = Field(max_length=320, pattern=EMAIL_PATTERN)
    
    role: Literal["admin", "builder", "reviewer", "viewer"]

    @field_validator("email")
    @classmethod
    def _lowercase(cls, value: str) -> str:
        return value.lower()


class InvitationOut(BaseModel):
    id: uuid.UUID
    email: str
    role: Role
    expires_at: datetime
    created_at: datetime
    expired: bool


class InvitationCreated(InvitationOut):
    

    token: str


class InvitationAccept(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=16, max_length=128)