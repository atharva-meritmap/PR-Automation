import hashlib
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.db.models import Article, Client, Journalist, Suppression, User
from app.services.outreach import (
    OutreachGuardrailError,
    approve_and_send_pitch,
    classify_and_record_reply,
    generate_pitch_for_journalist,
)


@pytest.mark.asyncio
async def test_pitch_generate_guardrails_dry_run_and_opt_out(db_session: AsyncSession) -> None:
    officer = User(
        email="outreach.officer@example.com",
        password_hash=hash_password("Pass1234!"),
        full_name="PR Officer",
        role="officer",
    )
    db_session.add(officer)
    await db_session.flush()

    client = Client(
        officer_id=officer.id,
        name="Atharva",
        domain="",
        industry="Distributed Systems",
        brand_voice="Principal Software Engineering Manager leading Azure Host Storage.",
        key_facts={"visa_category": "EB-1A", "role_title": "Principal Manager", "company": "Microsoft"},
    )
    db_session.add(client)

    journalist = Journalist(
        canonical_name="Maya Lin",
        outlet="TechDaily",
        beat="Cloud Infrastructure",
        email="maya.lin@techdaily.example.com",
        email_status="unverified",
        profile_source_url="https://techdaily.example.com/author/maya-lin",
    )
    db_session.add(journalist)
    await db_session.flush()

    article = Article(
        journalist_id=journalist.id,
        title="Scaling Cloud Storage Control Planes",
        url="https://techdaily.example.com/cloud-storage-control-planes",
        summary="Deep dive into storage virtualization reliability.",
        outlet="TechDaily",
        source_url="https://techdaily.example.com/feed",
        content_hash=hashlib.sha256(b"Scaling Cloud Storage Control Planes").hexdigest(),
        scraped_at=datetime.now(timezone.utc),
    )
    db_session.add(article)
    await db_session.commit()

    # 1. Generate grounded pitch
    pitch = await generate_pitch_for_journalist(
        db_session, client=client, journalist_id=journalist.id, officer=officer
    )
    assert pitch.status == "drafted"
    assert pitch.cited_article_id == article.id
    assert "Atharva" in pitch.body

    # 2. Guardrail blocks unverified email
    with pytest.raises(OutreachGuardrailError, match="verified"):
        await approve_and_send_pitch(db_session, pitch=pitch, approver=officer)

    # 3. Verify email -> dry_run send succeeds
    journalist.email_status = "verified"
    await db_session.flush()
    msg = await approve_and_send_pitch(db_session, pitch=pitch, approver=officer)
    assert msg.provider == "dry_run"
    assert msg.status == "sandboxed"
    assert pitch.status == "sent_dry_run"

    # 4. Cooldown blocks immediate second pitch
    pitch2 = await generate_pitch_for_journalist(
        db_session, client=client, journalist_id=journalist.id, officer=officer
    )
    with pytest.raises(OutreachGuardrailError, match="cooldown"):
        await approve_and_send_pitch(db_session, pitch=pitch2, approver=officer)

    # 5. Opt-out reply auto-suppresses journalist
    intent, _conf, suppressed = await classify_and_record_reply(
        db_session,
        pitch=pitch,
        reply_body="Please unsubscribe me and do not email again.",
    )
    assert intent == "opt_out"
    assert suppressed is True
    assert pitch.status == "opted_out"

    sup = (
        await db_session.execute(
            select(Suppression).where(Suppression.email == "maya.lin@techdaily.example.com")
        )
    ).scalar_one_or_none()
    assert sup is not None

