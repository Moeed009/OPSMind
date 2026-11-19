from enum import StrEnum


class Role(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    BUILDER = "builder"
    REVIEWER = "reviewer"
    VIEWER = "viewer"

class Region(StrEnum):
    EU = "eu"
    US = "us"

class Plan(StrEnum):
    FREE = "free"
    STARTER = "starter"
    PRO = "pro"

class SubscriptionStatus(StrEnum):
    INCOMPLETE = "incomplete"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"

class ConnectionStatus(StrEnum):
    PENDING = "pending"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    ERROR = "error"
    REVOKED = "revoked"

class EventStatus(StrEnum):
    RECEIVED = "received"
    PROCESSED = "processed"
    IGNORED = "ignored"
    FAILED = "failed"

class WorkflowVersionStatus(StrEnum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    TESTING = "testing"
    GATE_PASSED = "gate_passed"
    PUBLISHED = "published"
    PAUSED = "paused"
    FAILED = "failed"
    ARCHIVED = "archived"


class DepartmentStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    ARCHIVED = "archived"


class DepartmentType(StrEnum):
    SUPPORT = "support"
    SALES = "sales"
    FINANCE = "finance"
    LEGAL = "legal"
    OPERATIONS = "operations"


class DepartmentRole(StrEnum):
    ADMIN = "admin"
    BUILDER = "builder"
    REVIEWER = "reviewer"
    VIEWER = "viewer"

class TriggerSource(StrEnum):
    EVENT = "event"
    API = "api"
    SCHEDULE = "schedule"
    MANUAL = "manual"

class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_TIMER = "waiting_timer"
    WAITING_HUMAN = "waiting_human"
    WAITING_APPROVAL = "waiting_approval"
    NEEDS_REVIEW = "needs_review"
    RETRYING = "retrying"
    COMPLETED = "completed"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"

class RunStepStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"

class ActionStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"

class ReviewTaskStatus(StrEnum):
    OPEN = "open"
    ESCALATED = "escalated"
    CLAIMED = "claimed"
    APPROVED = "approved"
    EDITED = "edited"
    REJECTED = "rejected"

class DocumentSource(StrEnum):
    UPLOAD = "upload"
    CONNECTOR = "connector"

class DocumentStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"

class GuardrailDecision(StrEnum):
    BLOCKED = "blocked"
    MASKED = "masked"
    ESCALATED = "escalated"

class UsageMetric(StrEnum):
    RUNS = "runs"
    AI_TOKENS = "ai_tokens"
    CONNECTOR_CALLS = "connector_calls"
    STORAGE_BYTES = "storage_bytes"
