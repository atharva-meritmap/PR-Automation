import asyncio
from datetime import datetime, timedelta, timezone

from celery import Celery  # type: ignore[import-untyped]
from celery.schedules import crontab  # type: ignore[import-untyped]
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import Journalist
from app.db.session import get_session_factory
from app.services.email_verifier import verify_email_address
from app.services.journalists import upsert_scraped_journalist
from app.services.scraper import scrape_journalist_page

settings = get_settings()

celery_app = Celery(
    "pr_automation",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "refresh-stale-journalists-daily": {
            "task": "app.worker.refresh_stale_journalists_task",
            "schedule": crontab(hour=3, minute=0),
        }
    },
)


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def scrape_journalist_task(
    self, url: str, outlet: str = "", beat: str = "", rss_url: str | None = None
) -> str:
    async def _run() -> str:
        scraped = await scrape_journalist_page(url, outlet=outlet, beat=beat, rss_url=rss_url)
        async with get_session_factory()() as db:
            journalist = await upsert_scraped_journalist(db, scraped)
            return str(journalist.id)

    return asyncio.run(_run())


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def verify_journalist_email_task(self, journalist_id: str) -> str:
    async def _run() -> str:
        async with get_session_factory()() as db:
            res = await db.execute(select(Journalist).where(Journalist.id == journalist_id))
            journalist = res.scalar_one_or_none()
            if journalist is None:
                return "missing"
            result = await verify_email_address(journalist.email)
            journalist.email_status = result.status
            await db.commit()
            return result.status

    return asyncio.run(_run())


@celery_app.task
def refresh_stale_journalists_task() -> int:
    async def _run() -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=settings.REFRESH_STALE_DAYS)
        refreshed_count = 0
        async with get_session_factory()() as db:
            res = await db.execute(
                select(Journalist)
                .where(
                    Journalist.profile_source_url.is_not(None),
                    Journalist.profile_scraped_at < cutoff,
                )
                .limit(50)
            )
            for journalist in res.scalars().all():
                if not journalist.profile_source_url:
                    continue
                try:
                    scraped = await scrape_journalist_page(
                        journalist.profile_source_url,
                        outlet=journalist.outlet,
                        beat=journalist.beat,
                    )
                    await upsert_scraped_journalist(db, scraped)
                    refreshed_count += 1
                except Exception:
                    continue
        return refreshed_count

    return asyncio.run(_run())

