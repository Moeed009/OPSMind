import uuid
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from app.db.enums import Plan, Region, Role

if TYPE_CHECKING:
    from app.db.models import Tenant

SLUG_PATTERN = r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$"

class TenantCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(min_length=2, max_length=200)
    region: Region
    slug: str | None = Field(default=None, pattern=SLUG_PATTERN)

class TenantUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(min_length=2, max_length=200)

class TenantOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    region: Region
    plan: Plan
    role: Role

    @classmethod
    def from_tenant(cls, tenant: "Tenant", role: Role) -> "TenantOut":
        """Build the response for `tenant` as seen by a member holding `role`."""
        return cls(
            id=tenant.id,
            name=tenant.name,
            slug=tenant.slug,
            region=tenant.region,
            plan=tenant.plan,
            role=role,
        )

class MeOut(BaseModel):
    id: uuid.UUID
    email: str | None
    full_name: str | None
    tenants: list[TenantOut]
