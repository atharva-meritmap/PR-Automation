import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, get_scoped_client, require_admin
from app.core.config import get_settings
from app.db.models import Client, Journalist, Suppression, User
from app.db.session import get_db
from app.schemas.client import (
    BulkDiscoveryRequest,
    ClientCreate,
    ClientRead,
    ClientReassign,
    ClientUpdate,
    CuratedJournalistMatch,
)
from app.services.audit import record_audit_event
from app.services.decision import (
    HeuristicDecisionClassifier,
    RelevanceScore,
    get_decision_classifier,
)
from app.services.discovery import discover_journalists_for_client

router = APIRouter(prefix="/api/v1/clients", tags=["clients"])
_RELEVANCE_CACHE: dict[tuple[str, str], RelevanceScore] = {}


@router.post("", response_model=ClientRead, status_code=status.HTTP_201_CREATED)
async def create_client(
    payload: ClientCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Client:
    target_officer_id = current_user.id
    if current_user.role == "admin" and payload.officer_id is not None:
        officer_res = await db.execute(select(User).where(User.id == payload.officer_id))
        if officer_res.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Target officer not found",
            )
        target_officer_id = payload.officer_id

    client = Client(
        officer_id=target_officer_id,
        name=payload.name,
        domain=payload.domain.lower(),
        industry=payload.industry,
        brand_voice=payload.brand_voice,
        key_facts=payload.key_facts,
    )
    db.add(client)
    await db.flush()

    await record_audit_event(
        db,
        actor_user_id=current_user.id,
        action="client.create",
        entity_type="client",
        entity_id=str(client.id),
        metadata={"officer_id": str(target_officer_id), "name": client.name},
        ip_address=request.client.host if request.client else None,
    )
    await db.commit()
    await db.refresh(client)
    return client


@router.get("", response_model=list[ClientRead])
async def list_clients(
    officer_id: UUID | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Client]:
    stmt = select(Client).order_by(Client.created_at.desc())
    if current_user.role != "admin":
        stmt = stmt.where(Client.officer_id == current_user.id)
    elif officer_id is not None:
        stmt = stmt.where(Client.officer_id == officer_id)

    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/{client_id}", response_model=ClientRead)
async def get_client(client: Client = Depends(get_scoped_client)) -> Client:
    return client


@router.patch("/{client_id}", response_model=ClientRead)
async def update_client(
    payload: ClientUpdate,
    request: Request,
    client: Client = Depends(get_scoped_client),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Client:
    if payload.name is not None:
        client.name = payload.name
    if payload.domain is not None:
        client.domain = payload.domain.lower()
    if payload.industry is not None:
        client.industry = payload.industry
    if payload.brand_voice is not None:
        client.brand_voice = payload.brand_voice
    if payload.key_facts is not None:
        client.key_facts = payload.key_facts

    await record_audit_event(
        db,
        actor_user_id=current_user.id,
        action="client.update",
        entity_type="client",
        entity_id=str(client.id),
        metadata=payload.model_dump(exclude_none=True),
        ip_address=request.client.host if request.client else None,
    )
    await db.commit()
    await db.refresh(client)
    return client


@router.delete("/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_client(
    request: Request,
    client: Client = Depends(get_scoped_client),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    client_id_str = str(client.id)
    await db.delete(client)
    await record_audit_event(
        db,
        actor_user_id=current_user.id,
        action="client.delete",
        entity_type="client",
        entity_id=client_id_str,
        ip_address=request.client.host if request.client else None,
    )
    await db.commit()


@router.post("/{client_id}/reassign", response_model=ClientRead)
async def reassign_client(
    client_id: UUID,
    payload: ClientReassign,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Client:
    result = await db.execute(select(Client).where(Client.id == client_id))
    client = result.scalar_one_or_none()
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")

    officer_res = await db.execute(select(User).where(User.id == payload.officer_id))
    if officer_res.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Officer not found")

    previous_officer_id = str(client.officer_id)
    client.officer_id = payload.officer_id

    await record_audit_event(
        db,
        actor_user_id=admin.id,
        action="client.reassign",
        entity_type="client",
        entity_id=str(client.id),
        metadata={
            "previous_officer_id": previous_officer_id,
            "new_officer_id": str(payload.officer_id),
        },
        ip_address=request.client.host if request.client else None,
    )
    await db.commit()
    await db.refresh(client)
    return client


@router.get("/{client_id}/curated-journalists", response_model=list[CuratedJournalistMatch])
async def get_curated_journalists_for_client(
    client: Client = Depends(get_scoped_client),
    db: AsyncSession = Depends(get_db),
    refresh_scores: bool = Query(default=False),
) -> list[CuratedJournalistMatch]:
    """Rank and curate journalists specifically for this candidate client."""
    settings = get_settings()
    cooldown_cutoff = datetime.now(timezone.utc) - timedelta(days=settings.JOURNALIST_COOLDOWN_DAYS)

    suppressed_emails = {
        row.lower()
        for row in (await db.execute(select(Suppression.email))).scalars().all()
    }

    j_res = await db.execute(
        select(Journalist).options(selectinload(Journalist.articles))
    )
    journalists = list(j_res.scalars().all())

    clf = get_decision_classifier() if refresh_scores else HeuristicDecisionClassifier()
    client_context = f"{client.industry} {client.brand_voice[:160]}".strip()
    matches: list[CuratedJournalistMatch] = []
    for j in journalists:
        top_art = j.articles[0] if j.articles else None
        cache_key = (str(client.id), str(j.id))
        cached_score = None if refresh_scores else _RELEVANCE_CACHE.get(cache_key)
        if cached_score is not None:
            score = cached_score
        else:
            score = await asyncio.to_thread(
                clf.score_article_relevance,
                article_title=top_art.title if top_art else j.beat,
                article_summary=top_art.summary if top_art else f"{j.outlet} {j.beat}",
                client_name=client.name,
                client_industry=client_context,
            )
            if refresh_scores:
                _RELEVANCE_CACHE[cache_key] = score
        in_cooldown = bool(
            j.last_contacted_at is not None
            and j.last_contacted_at.replace(tzinfo=timezone.utc) >= cooldown_cutoff
        )
        suppressed = bool(j.email and j.email.lower() in suppressed_emails)
        matches.append(
            CuratedJournalistMatch(
                journalist_id=j.id,
                canonical_name=j.canonical_name,
                outlet=j.outlet,
                beat=j.beat,
                email=j.email,
                email_status=j.email_status,
                is_relevant=score.is_relevant,
                confidence=round(score.confidence, 4),
                matched_article_title=top_art.title if top_art else None,
                matched_article_url=top_art.url if top_art else None,
                in_cooldown=in_cooldown,
                suppressed=suppressed,
            )
        )

    matches.sort(
        key=lambda m: (
            m.email is not None,
            not m.suppressed,
            not m.in_cooldown,
            m.is_relevant,
            m.confidence,
        ),
        reverse=True,
    )
    return matches


@router.post("/{client_id}/discover-journalists", response_model=list[CuratedJournalistMatch])
async def bulk_discover_journalists_for_client(
    payload: BulkDiscoveryRequest,
    request: Request,
    client: Client = Depends(get_scoped_client),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[CuratedJournalistMatch]:
    """Automatically discover journalists in bulk for this client, then rank with Laya."""
    discovered = await discover_journalists_for_client(
        db,
        client,
        topic_override=payload.topic_override,
        max_results=payload.max_results,
    )
    await record_audit_event(
        db,
        actor_user_id=current_user.id,
        action="client.discover_journalists",
        entity_type="client",
        entity_id=str(client.id),
        metadata={"discovered_count": len(discovered), "topic": payload.topic_override or client.industry},
        ip_address=request.client.host if request.client else None,
    )
    await db.commit()
    return await get_curated_journalists_for_client(client=client, db=db, refresh_scores=False)
