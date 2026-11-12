from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_scoped_client
from app.db.models import Client, PrOpportunity, QwotedConnection, User
from app.db.session import get_db
from app.schemas.opportunity import (
    DiscoverOpportunitiesRequest,
    DraftPitchRequest,
    PitchSubmitResult,
    PrOpportunityRead,
    QwotedConnectRequest,
    QwotedStatusResponse,
    SubmitPitchRequest,
)
from app.services.audit import record_audit_event
from app.services.qwoted import (
    DiscoveredOpportunity,
    QwotedAuthError,
    discover_public_opportunities,
    draft_candidate_pitch_async,
    login_qwoted,
    score_opportunity_for_client,
    search_qwoted_opportunities,
    submit_qwoted_pitch,
    utc_now,
)

router = APIRouter(prefix="/api/v1", tags=["opportunities"])


@router.get("/qwoted/status", response_model=QwotedStatusResponse)
async def get_qwoted_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> QwotedStatusResponse:
    res = await db.execute(
        select(QwotedConnection).where(QwotedConnection.officer_id == current_user.id)
    )
    conn = res.scalar_one_or_none()
    if conn is None:
        return QwotedStatusResponse(connected=False, email=None)
    return QwotedStatusResponse(connected=True, email=conn.qwoted_email)


@router.post("/qwoted/connect", response_model=QwotedStatusResponse)
async def connect_qwoted(
    payload: QwotedConnectRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> QwotedStatusResponse:
    try:
        cookies = await login_qwoted(payload.email, payload.password)
    except QwotedAuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    res = await db.execute(
        select(QwotedConnection).where(QwotedConnection.officer_id == current_user.id)
    )
    conn = res.scalar_one_or_none()
    if conn is None:
        conn = QwotedConnection(
            officer_id=current_user.id,
            qwoted_email=payload.email.lower(),
            cookies_json=cookies,
        )
        db.add(conn)
    else:
        conn.qwoted_email = payload.email.lower()
        conn.cookies_json = cookies

    await record_audit_event(
        db,
        actor_user_id=current_user.id,
        action="qwoted.connect",
        entity_type="qwoted_connection",
        entity_id=str(current_user.id),
        metadata={"qwoted_email": payload.email.lower()},
        ip_address=request.client.host if request.client else None,
    )
    await db.commit()
    return QwotedStatusResponse(connected=True, email=conn.qwoted_email)


@router.post("/qwoted/disconnect", response_model=QwotedStatusResponse)
async def disconnect_qwoted(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> QwotedStatusResponse:
    res = await db.execute(
        select(QwotedConnection).where(QwotedConnection.officer_id == current_user.id)
    )
    conn = res.scalar_one_or_none()
    if conn is not None:
        await db.delete(conn)
        await db.commit()
    return QwotedStatusResponse(connected=False, email=None)


@router.get("/clients/{client_id}/opportunities", response_model=list[PrOpportunityRead])
async def list_client_opportunities(
    client: Client = Depends(get_scoped_client),
    db: AsyncSession = Depends(get_db),
) -> list[PrOpportunity]:
    res = await db.execute(
        select(PrOpportunity)
        .where(PrOpportunity.client_id == client.id)
        .order_by(PrOpportunity.is_relevant.desc(), PrOpportunity.confidence.desc(), PrOpportunity.created_at.desc())
    )
    return list(res.scalars().all())


@router.post("/clients/{client_id}/discover-opportunities", response_model=list[PrOpportunityRead])
async def discover_client_opportunities(
    payload: DiscoverOpportunitiesRequest,
    request: Request,
    client: Client = Depends(get_scoped_client),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[PrOpportunity]:
    """Discover curated PR opportunities from Qwoted (if connected) and public journalist callouts."""
    collected: list[DiscoveredOpportunity] = []
    topic = payload.topic_override.strip() or client.industry.strip() or client.name

    # 1. Qwoted live Algolia search if officer connected their Qwoted account
    q_res = await db.execute(
        select(QwotedConnection).where(QwotedConnection.officer_id == current_user.id)
    )
    conn = q_res.scalar_one_or_none()
    if conn is not None and conn.cookies_json:
        try:
            qwoted_hits = await search_qwoted_opportunities(
                conn.cookies_json,
                topic,
                max_hits=payload.max_results,
            )
            collected.extend(qwoted_hits)
        except QwotedAuthError:
            pass

    # 2. Public journalist callout / speaking / expert quote opportunities
    public_hits = await discover_public_opportunities(
        client,
        topic_override=payload.topic_override,
        max_results=payload.max_results,
    )
    collected.extend(public_hits)

    now = utc_now()
    for item in collected:
        is_rel, conf = score_opportunity_for_client(client, item)
        existing_res = await db.execute(
            select(PrOpportunity).where(
                PrOpportunity.client_id == client.id,
                PrOpportunity.source_platform == item.source_platform,
                PrOpportunity.external_id == item.external_id,
            )
        )
        existing = existing_res.scalar_one_or_none()
        if existing is None:
            opp = PrOpportunity(
                client_id=client.id,
                source_platform=item.source_platform,
                external_id=item.external_id,
                title=item.title,
                summary=item.summary,
                publication=item.publication,
                journalist_name=item.journalist_name,
                deadline=item.deadline,
                share_url=item.share_url,
                hashtags=item.hashtags,
                is_relevant=is_rel,
                confidence=conf,
                scraped_at=now,
            )
            db.add(opp)
        else:
            existing.title = item.title
            existing.summary = item.summary
            existing.publication = item.publication
            existing.journalist_name = item.journalist_name
            existing.deadline = item.deadline
            existing.is_relevant = is_rel
            existing.confidence = conf
            existing.scraped_at = now

    await record_audit_event(
        db,
        actor_user_id=current_user.id,
        action="client.discover_opportunities",
        entity_type="client",
        entity_id=str(client.id),
        metadata={"discovered_count": len(collected), "topic": topic},
        ip_address=request.client.host if request.client else None,
    )
    await db.commit()
    return await list_client_opportunities(client=client, db=db)


async def _get_scoped_opportunity(
    opportunity_id: UUID,
    current_user: User,
    db: AsyncSession,
) -> tuple[PrOpportunity, Client]:
    res = await db.execute(select(PrOpportunity).where(PrOpportunity.id == opportunity_id))
    opp = res.scalar_one_or_none()
    if opp is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Opportunity not found")
    c_res = await db.execute(select(Client).where(Client.id == opp.client_id))
    client = c_res.scalar_one_or_none()
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
    if current_user.role != "admin" and client.officer_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized for this client")
    return opp, client


@router.post("/opportunities/{opportunity_id}/draft-pitch", response_model=PrOpportunityRead)
async def draft_opportunity_pitch(
    opportunity_id: UUID,
    payload: DraftPitchRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PrOpportunity:
    opp, client = await _get_scoped_opportunity(opportunity_id, current_user, db)
    opp.draft_pitch = (
        payload.custom_pitch.strip()
        if payload.custom_pitch and payload.custom_pitch.strip()
        else await draft_candidate_pitch_async(client, opp)
    )
    opp.pitch_status = "drafted"
    await db.commit()
    await db.refresh(opp)
    return opp


@router.post("/opportunities/{opportunity_id}/submit-pitch", response_model=PitchSubmitResult)
async def submit_opportunity_pitch(
    opportunity_id: UUID,
    payload: SubmitPitchRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PitchSubmitResult:
    opp, _client = await _get_scoped_opportunity(opportunity_id, current_user, db)
    pitch_text = (payload.pitch_text or opp.draft_pitch).strip()
    if not pitch_text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Pitch text is empty. Draft or write a pitch before submitting.",
        )

    cookies: dict[str, str] = {}
    if opp.source_platform == "qwoted":
        q_res = await db.execute(
            select(QwotedConnection).where(QwotedConnection.officer_id == current_user.id)
        )
        conn = q_res.scalar_one_or_none()
        if conn:
            cookies = conn.cookies_json

    try:
        mode, detail = await submit_qwoted_pitch(
            cookies,
            source_request_id=opp.external_id,
            pitch_text=pitch_text,
            confirm_live_submit=payload.confirm_live_submit,
        )
    except QwotedAuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    opp.draft_pitch = pitch_text
    opp.pitch_status = "submitted" if mode == "qwoted_live" else "approved"

    await record_audit_event(
        db,
        actor_user_id=current_user.id,
        action="opportunity.submit_pitch",
        entity_type="pr_opportunity",
        entity_id=str(opp.id),
        metadata={"mode": mode, "source_platform": opp.source_platform},
        ip_address=request.client.host if request.client else None,
    )
    await db.commit()
    return PitchSubmitResult(
        opportunity_id=opp.id,
        pitch_status=opp.pitch_status,
        mode=mode,
        detail=detail,
    )

