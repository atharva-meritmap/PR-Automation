import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.routes import auth, clients, journalists, opportunities, pitches, users
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import get_engine

configure_logging()
logger = structlog.get_logger("pr_automation.api")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logger.info("app.startup", environment=settings.ENVIRONMENT, mail_provider=settings.MAIL_PROVIDER)
    yield
    logger.info("app.shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.APP_NAME, version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        db_status = "ok"
        redis_status = "ok"
        try:
            async with get_engine().connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception:
            db_status = "error"

        try:
            client = aioredis.from_url(settings.REDIS_URL, socket_connect_timeout=1)
            await client.ping()
            await client.aclose()
        except Exception:
            redis_status = "error"

        return {
            "status": "ok" if db_status == "ok" and redis_status == "ok" else "degraded",
            "database": db_status,
            "redis": redis_status,
            "mail_provider": settings.MAIL_PROVIDER,
        }

    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(clients.router)
    app.include_router(journalists.router)
    app.include_router(opportunities.router)
    app.include_router(pitches.router)

    return app


app = create_app()

