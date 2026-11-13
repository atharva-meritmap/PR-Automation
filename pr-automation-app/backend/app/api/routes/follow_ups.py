from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user
from app.db.models import Client, FollowUp, Journalist, Pitch, User
from app.db.session import get_db
from app.schemas.follow_up import FollowUpCreate, FollowUpRead, FollowUpUpdate
from app.services.audit import record_audit_event
from app.services.llm import curate_follow_up_timing

router = APIRouter(prefix="/api/v1", tags=["follow-ups"])


async def _get_authorized_client(
    db: AsyncSession, client_id: UUID, current_user: User
) -> Client:
    res = await db.execute(select(Client).where(Client.id == client_id))
    client = res.scalar_one_or_none()
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
    if current_user.role != "admin" and client.officer_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return client


def _to_read(fu: FollowUp, now: datetime) -> FollowUpRead:
    due_utc = fu.due_at if fu.due_at.tzinfo else fu.due_at.replace(tzinfo=timezone.utc)
    return FollowUpRead(
        id=fu.id,
        client_id=fu.client_id,
        officer_id=fu.officer_id,
        journalist_id=fu.journalist_id,
        pitch_id=fu.pitch_id,
        journalist_name=fu.journalist.canonical_name if fu.journalist else "Unknown Journalist",
        journalist_outlet=fu.journalist.outlet if fu.journalist else "",
        journalist_email=fu.journalist.email if fu.journalist else None,
        pitch_subject=fu.pitch.subject if fu.pitch else None,
        due_at=due_utc,
        status=fu.status,
        note=fu.note,
        is_overdue=(fu.status == "pending" and due_utc < now),
        completed_at=fu.completed_at,
        created_at=fu.created_at,
    )


@router.get("/clients/{client_id}/follow-ups", response_model=list[FollowUpRead])
async def list_client_follow_ups(
    client_id: UUID,
    status_filter: str | None = Query(default=None, alias="status"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[FollowUpRead]:
    await _get_authorized_client(db, client_id, current_user)
    stmt = (
        select(FollowUp)
        .options(selectinload(FollowUp.journalist), selectinload(FollowUp.pitch))
        .where(FollowUp.client_id == client_id)
        .order_by(FollowUp.due_at.asc())
    )
    if status_filter:
        stmt = stmt.where(FollowUp.status == status_filter)
    res = await db.execute(stmt)
    now = datetime.now(timezone.utc)
    return [_to_read(fu, now) for fu in res.scalars().all()]


@router.post(
    "/clients/{client_id}/follow-ups",
    response_model=FollowUpRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_follow_up(
    client_id: UUID,
    payload: FollowUpCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FollowUpRead:
    client = await _get_authorized_client(db, client_id, current_user)

    j_res = await db.execute(select(Journalist).where(Journalist.id == payload.journalist_id))
    journalist = j_res.scalar_one_or_none()
    if journalist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Journalist not found")

    pitch_subject = ""
    if payload.pitch_id is not None:
        p_res = await db.execute(
            select(Pitch).where(Pitch.id == payload.pitch_id, Pitch.client_id == client.id)
        )
        pitch_obj = p_res.scalar_one_or_none()
        if pitch_obj is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pitch not found")
        pitch_subject = pitch_obj.subject

    if payload.due_at is not None:
        due_at = (
            payload.due_at
            if payload.due_at.tzinfo
            else payload.due_at.replace(tzinfo=timezone.utc)
        )
        note_text = payload.note.strip()
    else:
        due_at, ai_reason = await curate_follow_up_timing(
            journalist_name=journalist.canonical_name,
            outlet=journalist.outlet,
            beat=journalist.beat,
            pitch_subject=pitch_subject or f"{client.industry} story",
        )
        user_note = payload.note.strip()
        note_text = f"{user_note} — {ai_reason}" if user_note else ai_reason

    fu = FollowUp(
        client_id=client.id,
        officer_id=current_user.id,
        journalist_id=journalist.id,
        pitch_id=payload.pitch_id,
        due_at=due_at,
        status="pending",
        note=note_text,
    )
    db.add(fu)
    await db.flush()

    await record_audit_event(
        db,
        actor_user_id=current_user.id,
        action="follow_up.create",
        entity_type="follow_up",
        entity_id=str(fu.id),
        metadata={"client_id": str(client.id), "journalist_id": str(journalist.id)},
        ip_address=request.client.host if request.client else None,
    )
    await db.commit()

    res = await db.execute(
        select(FollowUp)
        .options(selectinload(FollowUp.journalist), selectinload(FollowUp.pitch))
        .where(FollowUp.id == fu.id)
    )
    loaded = res.scalar_one()
    return _to_read(loaded, datetime.now(timezone.utc))


@router.patch("/follow-ups/{follow_up_id}", response_model=FollowUpRead)
async def update_follow_up(
    follow_up_id: UUID,
    payload: FollowUpUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FollowUpRead:
    res = await db.execute(
        select(FollowUp)
        .options(selectinload(FollowUp.journalist), selectinload(FollowUp.pitch))
        .where(FollowUp.id == follow_up_id)
    )
    fu = res.scalar_one_or_none()
    if fu is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Follow-up not found")

    await _get_authorized_client(db, fu.client_id, current_user)
    now = datetime.now(timezone.utc)

    if payload.snooze_days is not None:
        base = fu.due_at if fu.due_at.tzinfo else fu.due_at.replace(tzinfo=timezone.utc)
        fu.due_at = max(base, now) + timedelta(days=payload.snooze_days)
        fu.status = "pending"
        fu.completed_at = None
    elif payload.due_at is not None:
        fu.due_at = payload.due_at if payload.due_at.tzinfo else payload.due_at.replace(tzinfo=timezone.utc)

    if payload.status is not None:
        fu.status = payload.status
        fu.completed_at = now if payload.status in ("completed", "cancelled") else None

    if payload.note is not None:
        fu.note = payload.note.strip()

    await record_audit_event(
        db,
        actor_user_id=current_user.id,
        action="follow_up.update",
        entity_type="follow_up",
        entity_id=str(fu.id),
        metadata={"status": fu.status, "due_at": fu.due_at.isoformat()},
        ip_address=request.client.host if request.client else None,
    )
    await db.commit()
    await db.refresh(fu)
    return _to_read(fu, now)
