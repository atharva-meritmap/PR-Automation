from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.models import Article, Client, FollowUp, Journalist, Pitch, User
from app.services.llm import curate_follow_up_timing
from app.services.outreach import approve_and_send_pitch, classify_and_record_reply


@pytest.mark.asyncio
async def test_curate_follow_up_timing_avoids_weekends_and_uses_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")
    get_settings.cache_clear()

    def handler(request: httpx.Request) -> httpx.Response:
        # 2026-11-12 is Thursday; +2 days = Saturday -> should snap to Tuesday 2026-11-17
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"days_ahead": 2, "send_hour_utc": 15, "reason": "Morning editorial window for TechCrunch."}'
                        }
                    }
                ]
            },
        )

    try:
        ref = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)
        due_dt, reason = await curate_follow_up_timing(
            journalist_name="Maya Lin",
            outlet="TechCrunch",
            beat="AI",
            pitch_subject="Distributed AI perspective",
            now=ref,
            transport=httpx.MockTransport(handler),
        )
        assert due_dt.weekday() < 5
        assert due_dt.hour == 15
        assert "TechCrunch" in reason
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_follow_up_auto_schedule_reply_close_and_api(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    get_settings.cache_clear()

    officer = User(
        email="officer.fu@example.com",
        password_hash=hash_password("Pass1234!"),
        full_name="PR Officer",
        role="officer",
        is_active=True,
    )
    db_session.add(officer)
    await db_session.flush()

    cand = Client(
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
    db_session.add_all([cand, journalist])
    await db_session.flush()

    article = Article(
        journalist_id=journalist.id,
        title="AI Infra",
        url="https://techcrunch.com/ai-infra-fu",
        summary="Summary",
        source_url="https://techcrunch.com/feed",
        content_hash="hash-fu-1",
    )
    db_session.add(article)
    await db_session.flush()

    pitch = Pitch(
        client_id=cand.id,
        officer_id=officer.id,
        journalist_id=journalist.id,
        cited_article_id=article.id,
        tone="conversational",
        subject="AI Infra Follow-up Test",
        body="Pitch body",
        grounding_claims={},
        status="drafted",
    )
    db_session.add(pitch)
    await db_session.commit()
    await db_session.refresh(pitch)

    # 1. Sending pitch auto-schedules an AI-curated weekday follow-up
    await approve_and_send_pitch(db_session, pitch=pitch, approver=officer)
    await db_session.commit()

    fu_res = await db_session.execute(
        select(FollowUp).where(FollowUp.client_id == cand.id, FollowUp.journalist_id == journalist.id)
    )
    fu = fu_res.scalar_one()
    assert fu.status == "pending"
    assert fu.due_at.weekday() < 5

    # 2. Opt-out reply auto-cancels pending follow-up
    await classify_and_record_reply(
        db_session, pitch=pitch, reply_body="Please unsubscribe me from future pitches."
    )
    await db_session.commit()
    await db_session.refresh(fu)
    assert fu.status == "cancelled"

    # 3. API: login, create AI-curated follow-up (due_at=None), snooze, complete
    login_resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "officer.fu@example.com", "password": "Pass1234!"},
    )
    assert login_resp.status_code == 200
    headers = {"Authorization": f"Bearer {login_resp.json()['access_token']}"}

    create_resp = await client.post(
        f"/api/v1/clients/{cand.id}/follow-ups",
        headers=headers,
        json={"journalist_id": str(journalist.id), "note": "Check back on Q4 feature"},
    )
    assert create_resp.status_code == 201
    created = create_resp.json()
    assert created["status"] == "pending"
    assert "AI-curated" in created["note"]

    snooze_resp = await client.patch(
        f"/api/v1/follow-ups/{created['id']}",
        headers=headers,
        json={"snooze_days": 3},
    )
    assert snooze_resp.status_code == 200

    done_resp = await client.patch(
        f"/api/v1/follow-ups/{created['id']}",
        headers=headers,
        json={"status": "completed"},
    )
    assert done_resp.status_code == 200
    assert done_resp.json()["status"] == "completed"

