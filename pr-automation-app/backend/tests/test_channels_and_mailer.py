from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.models import Article, Client, EmailMessage, Journalist, Pitch, User
from app.services.discovery import fetch_bluesky_journalist_candidates
from app.services.mailer import dispatch_pitch_email
from app.services.outreach import OutreachGuardrailError, approve_and_send_pitch
from app.services.scraper import extract_article_markdown


@pytest.mark.asyncio
async def test_extract_article_markdown_html_and_jina_fallback() -> None:
    def html_handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "r.jina.ai":
            return httpx.Response(200, text="# Jina Title\n\nFull markdown from Jina.")
        if request.url.path == "/rich":
            return httpx.Response(
                200,
                text=(
                    "<html><body><h1>Distributed Systems at Scale</h1>"
                    "<p>This is a detailed engineering paragraph discussing low-latency vector indexing "
                    "and distributed inference engines across multi-region clusters.</p>"
                    "</body></html>"
                ),
            )
        return httpx.Response(403, text="Forbidden")

    transport = httpx.MockTransport(html_handler)
    md_html = await extract_article_markdown("https://example.com/rich", transport=transport)
    assert "Distributed Systems at Scale" in md_html
    assert "low-latency vector indexing" in md_html

    md_jina = await extract_article_markdown("https://example.com/blocked", transport=transport)
    assert "Full markdown from Jina." in md_jina


@pytest.mark.asyncio
async def test_fetch_bluesky_journalist_candidates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "public.api.bsky.app"
        return httpx.Response(
            200,
            json={
                "posts": [
                    {
                        "uri": "at://did:plc:123/app.bsky.feed.post/abc",
                        "author": {"handle": "sara.bsky.social", "displayName": "Sara Patel"},
                        "record": {
                            "text": "Covering biotech & AI hardware. Tips: sara@biotechbeat.com"
                        },
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        hits = await fetch_bluesky_journalist_candidates(
            http, "biotech", now=datetime.now(timezone.utc), limit=5
        )
    assert len(hits) == 1
    _, cand = hits[0]
    assert cand.canonical_name == "Sara Patel"
    assert cand.email == "sara@biotechbeat.com"
    assert cand.socials[0].platform == "bluesky"


@pytest.mark.asyncio
async def test_resend_mailer_and_daily_cap(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAIL_PROVIDER", "resend")
    monkeypatch.setenv("RESEND_API_KEY", "re_test_123")
    monkeypatch.setenv("RESEND_FROM_EMAIL", "pr@example.com")
    get_settings.cache_clear()

    def resend_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.resend.com"
        return httpx.Response(200, json={"id": "msg_resend_001"})

    try:
        msg_id, status = await dispatch_pitch_email(
            to_email="reporter@techcrunch.com",
            subject="Test Subject",
            body="Test Body",
            rfc_message_id="<test@pr-automation.local>",
            transport=httpx.MockTransport(resend_handler),
        )
        assert (msg_id, status) == ("msg_resend_001", "sent")
    finally:
        get_settings.cache_clear()

    # Verify daily cap enforcement
    monkeypatch.setenv("MAIL_PROVIDER", "dry_run")
    monkeypatch.setenv("MAX_DAILY_SENDS_PER_MAILBOX", "1")
    get_settings.cache_clear()

    officer = User(
        email="officer.cap@example.com",
        password_hash=hash_password("Pass1234!"),
        full_name="PR Officer",
        role="officer",
        is_active=True,
    )
    db_session.add(officer)
    await db_session.flush()

    client_obj = Client(
        officer_id=officer.id,
        name="Atharva",
        domain="",
        industry="Distributed AI",
        brand_voice="Built 1M RPS engine",
        key_facts={},
    )
    journalist = Journalist(
        canonical_name="Maya Lin",
        outlet="TechCrunch",
        beat="AI",
        email="maya@techcrunch.com",
        email_status="verified",
        confidence_score=0.9,
    )
    db_session.add_all([client_obj, journalist])
    await db_session.flush()

    article = Article(
        journalist_id=journalist.id,
        title="AI Infra",
        url="https://techcrunch.com/ai-infra",
        summary="Summary",
        source_url="https://techcrunch.com/feed",
        content_hash="hash-cap-1",
    )
    db_session.add(article)
    await db_session.flush()

    pitch1 = Pitch(
        client_id=client_obj.id,
        officer_id=officer.id,
        journalist_id=journalist.id,
        cited_article_id=article.id,
        tone="conversational",
        subject="Pitch 1",
        body="Body 1",
        grounding_claims={},
        status="drafted",
    )
    db_session.add(pitch1)
    await db_session.flush()

    # Seed one outbound message within last 24h to hit the cap of 1
    db_session.add(
        EmailMessage(
            pitch_id=pitch1.id,
            direction="outbound",
            provider="dry_run",
            status="sandboxed",
            sent_at=datetime.now(timezone.utc),
            raw_headers={},
            body_snippet="prev",
        )
    )
    await db_session.commit()

    try:
        with pytest.raises(OutreachGuardrailError, match="daily mailbox cap"):
            await approve_and_send_pitch(db_session, pitch=pitch1, approver=officer)
    finally:
        get_settings.cache_clear()

