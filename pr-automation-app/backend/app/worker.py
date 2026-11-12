import asyncio
import email as std_email
import imaplib
from datetime import datetime, timedelta, timezone

from celery import Celery  # type: ignore[import-untyped]
from celery.schedules import crontab  # type: ignore[import-untyped]
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import Client, EmailMessage, Journalist, Pitch
from app.db.session import get_session_factory
from app.services.discovery import discover_journalists_for_client
from app.services.email_verifier import verify_email_address
from app.services.journalists import upsert_scraped_journalist
from app.services.outreach import classify_and_record_reply
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
        },
        "overnight-bulk-discovery-daily": {
            "task": "app.worker.overnight_bulk_discovery_task",
            "schedule": crontab(hour=2, minute=0),
        },
        "poll-imap-replies-every-15m": {
            "task": "app.worker.poll_imap_replies_task",
            "schedule": crontab(minute="*/15"),
        },
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


@celery_app.task
def overnight_bulk_discovery_task() -> int:
    async def _run() -> int:
        total_discovered = 0
        async with get_session_factory()() as db:
            res = await db.execute(select(Client).limit(25))
            for client in res.scalars().all():
                try:
                    found = await discover_journalists_for_client(db, client, max_results=10)
                    total_discovered += len(found)
                except Exception:
                    continue
        return total_discovered

    return asyncio.run(_run())


@celery_app.task
def poll_imap_replies_task() -> int:
    if not settings.IMAP_HOST or not settings.IMAP_USER or not settings.IMAP_PASSWORD:
        return 0

    def _fetch_unseen() -> list[tuple[str, str]]:
        messages: list[tuple[str, str]] = []
        with imaplib.IMAP4_SSL(settings.IMAP_HOST, settings.IMAP_PORT) as mail:
            mail.login(settings.IMAP_USER, settings.IMAP_PASSWORD)
            mail.select("INBOX")
            status, data = mail.search(None, "UNSEEN")
            if status != "OK" or not data or not data[0]:
                return []
            for num in data[0].split()[:25]:
                _, msg_data = mail.fetch(num, "(RFC822)")
                for part in msg_data:
                    if isinstance(part, tuple):
                        parsed = std_email.message_from_bytes(part[1])
                        in_reply_to = str(parsed.get("In-Reply-To") or "").strip()
                        body_text = ""
                        if parsed.is_multipart():
                            for sub in parsed.walk():
                                if sub.get_content_type() == "text/plain":
                                    payload = sub.get_payload(decode=True)
                                    if isinstance(payload, bytes):
                                        body_text = payload.decode("utf-8", errors="ignore")
                                    break
                        else:
                            payload = parsed.get_payload(decode=True)
                            if isinstance(payload, bytes):
                                body_text = payload.decode("utf-8", errors="ignore")
                        if in_reply_to and body_text.strip():
                            messages.append((in_reply_to, body_text.strip()))
        return messages

    async def _run() -> int:
        fetched = await asyncio.to_thread(_fetch_unseen)
        if not fetched:
            return 0
        processed = 0
        async with get_session_factory()() as db:
            for in_reply_to, reply_body in fetched:
                msg_res = await db.execute(
                    select(EmailMessage).where(EmailMessage.rfc_message_id == in_reply_to)
                )
                outbound = msg_res.scalar_one_or_none()
                if outbound is None:
                    continue
                pitch_res = await db.execute(select(Pitch).where(Pitch.id == outbound.pitch_id))
                pitch = pitch_res.scalar_one_or_none()
                if pitch is None:
                    continue
                await classify_and_record_reply(db, pitch=pitch, reply_body=reply_body)
                await db.commit()
                processed += 1
        return processed

    return asyncio.run(_run())

