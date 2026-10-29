import redis
from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.db.session import get_engine

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    app: str
    environment: str


class ReadinessResponse(BaseModel):
    status: str
    database: bool
    redis: bool


def check_database() -> bool:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except SQLAlchemyError:
        return False


def check_redis() -> bool:
    try:
        client = redis.Redis.from_url(get_settings().redis_url, socket_connect_timeout=2)
        return bool(client.ping())
    except redis.RedisError:
        return False


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(status="ok", app=settings.app_name, environment=settings.environment)


@router.get("/health/ready", response_model=ReadinessResponse)
def ready(response: Response) -> ReadinessResponse:
    database_ok = check_database()
    redis_ok = check_redis()
    is_ready = database_ok and redis_ok
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(
        status="ready" if is_ready else "degraded",
        database=database_ok,
        redis=redis_ok,
    )