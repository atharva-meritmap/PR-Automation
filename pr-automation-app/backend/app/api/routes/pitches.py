from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_scoped_client
from app.db.models import Article, Client, EmailMessage, Journalist, Pitch, User
from app.db.session import get_db
from app.schemas.pitch import (
    EmailMessageRead,
    PitchGenerateRequest,
    PitchRead,
    PitchUpdateRequest,
    ReplyClassificationRead,
    ReplyIngestRequest,
)
from app.services.audit import record_audit_event
from app.services.outreach import (
    OutreachGuardrailError,
    approve_and_send_pitch,
    classify_and_record_reply,
    generate_pitch_for_journalist,
)

router = APIRouter(prefix="/api/v1", tags=["pitches"])


async def _hydrate_pitch_read(db: AsyncSession, pitch: Pitch) -> PitchRead:
    j = (await db.execute(select(Journalist).where(Journalist.id == pitch.journalist_id))).scalar_one_or_none()
    a = (await db.execute(select(Article).where(Article.id == pitch.cited_article_id))).scalar_one_or_none()
    return PitchRead(
        id=pitch.id,
        client_id=pitch.client_id,
        officer_id=pitch.officer_id,
        journalist_id=pitch.journalist_id,
        cited_article_id=pitch.cited_article_id,
        tone=pitch.tone,
        subject=pitch.subject,
        body=pitch.body,
        grounding_claims=pitch.grounding_claims,
        status=pitch.status,
        approved_by=pitch.approved_by,
        approved_at=pitch.approved_at,
        created_at=pitch.created_at,
        journalist_name=j.canonical_name if j else "",
        journalist_email=j.email if j else None,
        journalist_email_status=j.email_status if j else "missing",
        journalist_outlet=j.outlet if j else "",
        cited_article_title=a.title if a else "",
        cited_article_url=a.url if a else "",
    )


async def _get_scoped_pitch(pitch_id: UUID, current_user: User, db: AsyncSession) -> Pitch:
    pitch = (await db.execute(select(Pitch).where(Pitch.id == pitch_id))).scalar_one_or_none()
    if pitch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pitch not found")
    client = (await db.execute(select(Client).where(Client.id == pitch.client_id))).scalar_one_or_none()
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
    if current_user.role != "admin" and client.officer_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized for this client")
    return pitch


@router.get("/clients/{client_id}/pitches", response_model=list[PitchRead])
async def list_client_pitches(
    client: Client = Depends(get_scoped_client),
    db: AsyncSession = Depends(get_db),
) -> list[PitchRead]:
    rows = (
        await db.execute(
            select(Pitch).where(Pitch.client_id == client.id).order_by(Pitch.created_at.desc())
        )
    ).scalars().all()
    return [await _hydrate_pitch_read(db, p) for p in rows]


@router.post("/pitches/generate", response_model=PitchRead, status_code=status.HTTP_201_CREATED)
async def generate_pitch(
    payload: PitchGenerateRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PitchRead:
    client = (await db.execute(select(Client).where(Client.id == payload.client_id))).scalar_one_or_none()
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
    if current_user.role != "admin" and client.officer_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized for this client")

    try:
        pitch = await generate_pitch_for_journalist(
            db,
            client=client,
            journalist_id=payload.journalist_id,
            officer=current_user,
            tone=payload.tone,
        )
    except OutreachGuardrailError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await record_audit_event(
        db,
        actor_user_id=current_user.id,
        action="pitch.generate",
        entity_type="pitch",
        entity_id=str(pitch.id),
        metadata={"client_id": str(client.id), "journalist_id": str(payload.journalist_id)},
        ip_address=request.client.host if request.client else None,
    )
    await db.commit()
    await db.refresh(pitch)
    return await _hydrate_pitch_read(db, pitch)


@router.patch("/pitches/{pitch_id}", response_model=PitchRead)
async def update_pitch(
    pitch_id: UUID,
    payload: PitchUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PitchRead:
    pitch = await _get_scoped_pitch(pitch_id, current_user, db)
    if pitch.status != "drafted":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only drafted pitches can be edited.",
        )
    if payload.subject is not None:
        pitch.subject = payload.subject
    if payload.body is not None:
        pitch.body = payload.body
    await db.commit()
    await db.refresh(pitch)
    return await _hydrate_pitch_read(db, pitch)


@router.post("/pitches/{pitch_id}/approve-send", response_model=PitchRead)
async def approve_send_pitch(
    pitch_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PitchRead:
    pitch = await _get_scoped_pitch(pitch_id, current_user, db)
    if pitch.status not in ("drafted", "approved"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Pitch cannot be sent from status '{pitch.status}'.",
        )
    try:
        msg = await approve_and_send_pitch(db, pitch=pitch, approver=current_user)
    except OutreachGuardrailError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await record_audit_event(
        db,
        actor_user_id=current_user.id,
        action="pitch.approve_send",
        entity_type="pitch",
        entity_id=str(pitch.id),
        metadata={"provider": msg.provider, "message_status": msg.status},
        ip_address=request.client.host if request.client else None,
    )
    await db.commit()
    await db.refresh(pitch)
    return await _hydrate_pitch_read(db, pitch)


@router.post("/pitches/{pitch_id}/simulate-reply", response_model=ReplyClassificationRead)
async def simulate_pitch_reply(
    pitch_id: UUID,
    payload: ReplyIngestRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReplyClassificationRead:
    pitch = await _get_scoped_pitch(pitch_id, current_user, db)
    intent, confidence, suppressed = await classify_and_record_reply(
        db, pitch=pitch, reply_body=payload.body
    )
    await record_audit_event(
        db,
        actor_user_id=current_user.id,
        action="pitch.reply_classified",
        entity_type="pitch",
        entity_id=str(pitch.id),
        metadata={"intent": intent, "confidence": confidence, "suppressed": suppressed},
        ip_address=request.client.host if request.client else None,
    )
    await db.commit()
    detail = (
        "Opt-out detected: journalist added to suppression list."
        if suppressed
        else f"Classified reply as '{intent}' ({int(confidence * 100)}%)."
    )
    return ReplyClassificationRead(
        pitch_id=pitch.id,
        intent=intent,
        confidence=confidence,
        suppressed=suppressed,
        pitch_status=pitch.status,
        detail=detail,
    )


@router.get("/pitches/{pitch_id}/messages", response_model=list[EmailMessageRead])
async def list_pitch_messages(
    pitch_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[EmailMessage]:
    pitch = await _get_scoped_pitch(pitch_id, current_user, db)
    rows = (
        await db.execute(select(EmailMessage).where(EmailMessage.pitch_id == pitch.id))
    ).scalars().all()
    return list(rows)

