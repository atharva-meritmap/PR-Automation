import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.db.models import Article, Client, EmailMessage, Journalist, Pitch, Suppression, User
from app.services.decision import HeuristicDecisionClassifier
from app.services.llm import generate_openrouter_pitch
from app.services.mailer import MailDispatchError, dispatch_pitch_email
from app.services.scraper import extract_article_markdown


class OutreachGuardrailError(Exception):
    """Raised when a pitch violates suppression, cooldown, or verification guardrails."""


def _fallback_pitch_body(client: Client, journalist: Journalist, article: Article) -> str:
    role = str(client.key_facts.get("role_title", "")).strip()
    company = str(client.key_facts.get("company", "")).strip()
    role_line = f"{role} at {company}" if role and company else (role or company or client.industry)
    facts = client.brand_voice.strip() or f"Deep expertise in {client.industry}."
    return (
        f"Hi {journalist.canonical_name},\n\n"
        f"I enjoyed your recent {journalist.outlet} piece, \"{article.title}\". "
        f"Given your coverage of {journalist.beat or client.industry}, I wanted to introduce "
        f"{client.name} ({role_line}) as an expert source for upcoming stories.\n\n"
        f"Verified background:\n{facts}\n\n"
        f"Happy to share concise commentary or coordinate a brief interview at your convenience.\n\n"
        f"Best regards,\n"
        f"PR Team on behalf of {client.name}"
    )


async def generate_pitch_for_journalist(
    db: AsyncSession,
    *,
    client: Client,
    journalist_id: uuid.UUID,
    officer: User,
    tone: str = "conversational",
) -> Pitch:
    """Generate a grounded pitch draft for a journalist citing one of their real scraped articles."""
    j_res = await db.execute(
        select(Journalist)
        .options(selectinload(Journalist.articles))
        .where(Journalist.id == journalist_id)
    )
    journalist = j_res.scalar_one_or_none()
    if journalist is None:
        raise OutreachGuardrailError("Journalist not found")
    if not journalist.articles:
        raise OutreachGuardrailError(
            "Journalist has no scraped articles to cite. Refresh the journalist profile first."
        )

    article = journalist.articles[0]
    role = str(client.key_facts.get("role_title", "")).strip()
    company = str(client.key_facts.get("company", "")).strip()
    role_line = f"{role} at {company}" if role and company else (role or company or client.industry)
    approved_facts = client.brand_voice.strip() or f"Expertise in {client.industry}."
    article_context = article.summary.strip()
    if len(article_context) < 160 and article.url:
        extracted_md = await extract_article_markdown(article.url)
        if extracted_md:
            article_context = extracted_md

    llm_body = await generate_openrouter_pitch(
        client_name=client.name,
        role_line=role_line,
        industry=client.industry,
        approved_facts=approved_facts,
        target_title=article.title,
        target_publication=journalist.outlet,
        journalist_name=journalist.canonical_name,
        target_summary=article_context,
    )
    body = llm_body or _fallback_pitch_body(client, journalist, article)
    subject = f"Expert perspective on {client.industry}: {client.name} ({journalist.outlet} follow-up)"[:300]

    pitch = Pitch(
        client_id=client.id,
        officer_id=officer.id,
        journalist_id=journalist.id,
        cited_article_id=article.id,
        tone=tone,
        subject=subject,
        body=body,
        grounding_claims={
            "client_name": client.name,
            "cited_article_title": article.title,
            "cited_article_url": article.url,
            "approved_facts": approved_facts[:400],
            "generator": "openrouter" if llm_body else "deterministic_template",
        },
        status="drafted",
    )
    db.add(pitch)
    await db.flush()
    return pitch


async def approve_and_send_pitch(
    db: AsyncSession,
    *,
    pitch: Pitch,
    approver: User,
) -> EmailMessage:
    """Enforce suppression, cooldown, and email verification guardrails, then dispatch (dry_run in dev)."""
    settings = get_settings()
    now = datetime.now(timezone.utc)

    j_res = await db.execute(select(Journalist).where(Journalist.id == pitch.journalist_id))
    journalist = j_res.scalar_one_or_none()
    if journalist is None:
        raise OutreachGuardrailError("Journalist not found")

    if not journalist.email:
        raise OutreachGuardrailError("Cannot send pitch: journalist has no email address.")

    if journalist.email_status in ("missing", "invalid"):
        raise OutreachGuardrailError(
            f"Cannot send pitch: journalist email status is '{journalist.email_status}'."
        )

    if settings.REQUIRE_VERIFIED_EMAIL and journalist.email_status != "verified":
        raise OutreachGuardrailError(
            f"Email must be verified before sending (current status: '{journalist.email_status}'). "
            "Click 'Verify Email' first."
        )

    sup_res = await db.execute(
        select(Suppression).where(Suppression.email == journalist.email.lower())
    )
    if sup_res.scalar_one_or_none() is not None:
        raise OutreachGuardrailError("Blocked: journalist email is on the suppression list.")

    cooldown_cutoff = now - timedelta(days=settings.JOURNALIST_COOLDOWN_DAYS)
    if (
        journalist.last_contacted_at is not None
        and journalist.last_contacted_at.replace(tzinfo=timezone.utc) >= cooldown_cutoff
    ):
        raise OutreachGuardrailError(
            f"Blocked: journalist is within the {settings.JOURNALIST_COOLDOWN_DAYS}-day cooldown window."
        )

    daily_cutoff = now - timedelta(hours=24)
    daily_count_res = await db.execute(
        select(func.count(EmailMessage.id)).where(
            EmailMessage.direction == "outbound",
            EmailMessage.sent_at >= daily_cutoff,
        )
    )
    daily_sent = int(daily_count_res.scalar() or 0)
    if daily_sent >= settings.MAX_DAILY_SENDS_PER_MAILBOX:
        raise OutreachGuardrailError(
            f"Blocked: daily mailbox cap of {settings.MAX_DAILY_SENDS_PER_MAILBOX} sends reached."
        )

    is_dry_run = settings.MAIL_PROVIDER == "dry_run"
    rfc_message_id = f"<{uuid.uuid4().hex}@pr-automation.local>"
    try:
        provider_message_id, delivery_status = await dispatch_pitch_email(
            to_email=journalist.email,
            subject=pitch.subject,
            body=pitch.body,
            rfc_message_id=rfc_message_id,
        )
    except MailDispatchError as exc:
        raise OutreachGuardrailError(str(exc)) from exc

    msg = EmailMessage(
        pitch_id=pitch.id,
        direction="outbound",
        provider=settings.MAIL_PROVIDER,
        provider_message_id=provider_message_id,
        rfc_message_id=rfc_message_id,
        status=delivery_status,
        sent_at=now,
        raw_headers={"to": journalist.email, "subject": pitch.subject, "dry_run": is_dry_run},
        body_snippet=pitch.body[:400],
    )
    db.add(msg)

    pitch.approved_by = approver.id
    pitch.approved_at = now
    pitch.status = "sent_dry_run" if is_dry_run else "sent"
    journalist.last_contacted_at = now
    await db.flush()
    return msg


async def classify_and_record_reply(
    db: AsyncSession,
    *,
    pitch: Pitch,
    reply_body: str,
) -> tuple[str, float, bool]:
    """Classify an incoming reply, log EmailMessage, and auto-suppress on opt_out."""
    settings = get_settings()
    now = datetime.now(timezone.utc)

    clf = HeuristicDecisionClassifier()
    classification = await asyncio.to_thread(clf.classify_reply, reply_body)

    msg = EmailMessage(
        pitch_id=pitch.id,
        direction="inbound",
        provider=settings.MAIL_PROVIDER,
        status="received",
        received_at=now,
        body_snippet=reply_body[:400],
    )
    db.add(msg)

    suppressed = False
    if classification.intent == "opt_out":
        j_res = await db.execute(select(Journalist).where(Journalist.id == pitch.journalist_id))
        journalist = j_res.scalar_one_or_none()
        if journalist and journalist.email:
            sup_res = await db.execute(
                select(Suppression).where(Suppression.email == journalist.email.lower())
            )
            if sup_res.scalar_one_or_none() is None:
                db.add(
                    Suppression(
                        email=journalist.email.lower(),
                        journalist_id=journalist.id,
                        reason="opt_out",
                        source="reply_classifier",
                    )
                )
            suppressed = True
        pitch.status = "opted_out"
    else:
        pitch.status = f"replied_{classification.intent}"

    await db.flush()
    return classification.intent, round(classification.confidence, 4), suppressed

