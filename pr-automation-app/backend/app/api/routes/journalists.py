from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user
from app.db.models import Client, Journalist, User
from app.db.session import get_db
from app.schemas.journalist import (
    EmailVerificationResponse,
    JournalistRead,
    JournalistScrapeRequest,
    RelevanceScoreResponse,
)
from app.services.audit import record_audit_event
from app.services.decision import get_decision_classifier
from app.services.email_verifier import verify_email_address
from app.services.journalists import upsert_scraped_journalist
from app.services.scraper import ScrapeBlockedError, scrape_journalist_page

router = APIRouter(prefix="/api/v1/journalists", tags=["journalists"])


@router.post("/scrape", response_model=JournalistRead, status_code=status.HTTP_201_CREATED)
async def scrape_journalist(
    payload: JournalistScrapeRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Journalist:
    try:
        scraped = await scrape_journalist_page(
            payload.url,
            outlet=payload.outlet,
            beat=payload.beat,
            rss_url=payload.rss_url,
        )
    except ScrapeBlockedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch profile URL: {exc}",
        ) from exc

    journalist = await upsert_scraped_journalist(db, scraped)

    if payload.verify_email and journalist.email:
        verification = await verify_email_address(journalist.email)
        journalist.email_status = verification.status
        await db.commit()
        await db.refresh(journalist)

    await record_audit_event(
        db,
        actor_user_id=current_user.id,
        action="journalist.scrape",
        entity_type="journalist",
        entity_id=str(journalist.id),
        metadata={"source_url": payload.url, "email_status": journalist.email_status},
        ip_address=request.client.host if request.client else None,
    )
    await db.commit()
    return journalist


@router.get("", response_model=list[JournalistRead])
async def list_journalists(
    outlet: str | None = Query(default=None),
    beat: str | None = Query(default=None),
    email_status: str | None = Query(default=None),
    q: str | None = Query(default=None),
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Journalist]:
    stmt = (
        select(Journalist)
        .options(selectinload(Journalist.socials), selectinload(Journalist.articles))
        .order_by(Journalist.updated_at.desc())
    )
    if outlet:
        stmt = stmt.where(func.lower(Journalist.outlet).contains(outlet.lower()))
    if beat:
        stmt = stmt.where(func.lower(Journalist.beat).contains(beat.lower()))
    if email_status:
        stmt = stmt.where(Journalist.email_status == email_status)
    if q:
        stmt = stmt.where(func.lower(Journalist.canonical_name).contains(q.lower()))

    res = await db.execute(stmt)
    return list(res.scalars().all())


@router.get("/{journalist_id}", response_model=JournalistRead)
async def get_journalist(
    journalist_id: UUID,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Journalist:
    res = await db.execute(
        select(Journalist)
        .options(selectinload(Journalist.socials), selectinload(Journalist.articles))
        .where(Journalist.id == journalist_id)
    )
    journalist = res.scalar_one_or_none()
    if journalist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Journalist not found")
    return journalist


@router.post("/{journalist_id}/verify-email", response_model=EmailVerificationResponse)
async def verify_journalist_email(
    journalist_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EmailVerificationResponse:
    res = await db.execute(select(Journalist).where(Journalist.id == journalist_id))
    journalist = res.scalar_one_or_none()
    if journalist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Journalist not found")

    result = await verify_email_address(journalist.email)
    journalist.email_status = result.status

    await record_audit_event(
        db,
        actor_user_id=current_user.id,
        action="journalist.verify_email",
        entity_type="journalist",
        entity_id=str(journalist.id),
        metadata={"status": result.status, "mx_host": result.mx_host},
        ip_address=request.client.host if request.client else None,
    )
    await db.commit()

    return EmailVerificationResponse(
        journalist_id=journalist.id,
        email=journalist.email,
        email_status=result.status,
        mx_host=result.mx_host,
        detail=result.detail,
    )


@router.post("/{journalist_id}/refresh", response_model=JournalistRead)
async def refresh_journalist(
    journalist_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Journalist:
    res = await db.execute(select(Journalist).where(Journalist.id == journalist_id))
    journalist = res.scalar_one_or_none()
    if journalist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Journalist not found")
    if not journalist.profile_source_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Journalist has no profile_source_url to refresh",
        )

    try:
        scraped = await scrape_journalist_page(
            journalist.profile_source_url,
            outlet=journalist.outlet,
            beat=journalist.beat,
        )
    except ScrapeBlockedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to refresh profile: {exc}",
        ) from exc

    updated = await upsert_scraped_journalist(db, scraped)
    await record_audit_event(
        db,
        actor_user_id=current_user.id,
        action="journalist.refresh",
        entity_type="journalist",
        entity_id=str(updated.id),
        ip_address=request.client.host if request.client else None,
    )
    await db.commit()
    return updated


@router.post("/{journalist_id}/score-relevance", response_model=RelevanceScoreResponse)
async def score_journalist_relevance(
    journalist_id: UUID,
    client_id: UUID = Query(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RelevanceScoreResponse:
    c_res = await db.execute(select(Client).where(Client.id == client_id))
    client = c_res.scalar_one_or_none()
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
    if current_user.role != "admin" and client.officer_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    j_res = await db.execute(
        select(Journalist)
        .options(selectinload(Journalist.articles))
        .where(Journalist.id == journalist_id)
    )
    journalist = j_res.scalar_one_or_none()
    if journalist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Journalist not found")

    top_article = journalist.articles[0] if journalist.articles else None
    clf = get_decision_classifier()
    score = clf.score_article_relevance(
        article_title=top_article.title if top_article else journalist.beat,
        article_summary=top_article.summary if top_article else journalist.outlet,
        client_name=client.name,
        client_industry=client.industry,
    )
    return RelevanceScoreResponse(
        journalist_id=journalist.id,
        client_id=client.id,
        article_id=top_article.id if top_article else None,
        is_relevant=score.is_relevant,
        confidence=score.confidence,
    )

