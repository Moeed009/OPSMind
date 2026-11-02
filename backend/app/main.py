
from fastapi import FastAPI

from app.api import health
from app.api.v1.router import router as v1_router
from app.core.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
    )

    app.include_router(
        health.router,
        prefix=settings.api_prefix,
    )

    app.include_router(
        v1_router,
        prefix=settings.api_prefix,
    )

    return app


app = create_app()
