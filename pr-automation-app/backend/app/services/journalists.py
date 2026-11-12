from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Article, Journalist, JournalistSocial
from app.services.scraper import ScrapedJournalist


async def upsert_scraped_journalist(
    db: AsyncSession,
    scraped: ScrapedJournalist,
) -> Journalist:
    """Deduplicate and upsert a journalist globally across clients, preserving provenance."""
    existing: Journalist | None = None

    if scraped.email:
        res = await db.execute(
            select(Journalist)
            .options(selectinload(Journalist.socials), selectinload(Journalist.articles))
            .where(func.lower(Journalist.email) == scraped.email.lower())
        )
        existing = res.scalar_one_or_none()

    if existing is None:
        res = await db.execute(
            select(Journalist)
            .options(selectinload(Journalist.socials), selectinload(Journalist.articles))
            .where(
                func.lower(Journalist.canonical_name) == scraped.canonical_name.lower(),
                func.lower(Journalist.outlet) == scraped.outlet.lower(),
            )
        )
        existing = res.scalar_one_or_none()

    if existing is None:
        journalist = Journalist(
            canonical_name=scraped.canonical_name,
            outlet=scraped.outlet,
            beat=scraped.beat,
            email=scraped.email,
            email_status="unverified" if scraped.email else "missing",
            email_source_url=scraped.email_source_url,
            email_scraped_at=scraped.email_scraped_at,
            profile_source_url=scraped.profile_source_url,
            profile_scraped_at=scraped.profile_scraped_at,
            confidence_score=scraped.confidence_score,
        )
        db.add(journalist)
        await db.flush()
    else:
        journalist = existing
        journalist.canonical_name = scraped.canonical_name
        journalist.outlet = scraped.outlet
        if scraped.beat:
            journalist.beat = scraped.beat
        if scraped.email and not journalist.email:
            journalist.email = scraped.email
            journalist.email_status = "unverified"
            journalist.email_source_url = scraped.email_source_url
            journalist.email_scraped_at = scraped.email_scraped_at
        journalist.profile_source_url = scraped.profile_source_url
        journalist.profile_scraped_at = scraped.profile_scraped_at
        journalist.confidence_score = max(journalist.confidence_score, scraped.confidence_score)

    existing_socials = {
        (s.platform, s.handle_or_url) for s in (journalist.socials if existing else [])
    }
    for social in scraped.socials:
        key = (social.platform, social.handle_or_url)
        if key not in existing_socials:
            db.add(
                JournalistSocial(
                    journalist_id=journalist.id,
                    platform=social.platform,
                    handle_or_url=social.handle_or_url,
                    source_url=social.source_url,
                    scraped_at=social.scraped_at,
                )
            )
            existing_socials.add(key)

    for art in scraped.articles:
        art_res = await db.execute(select(Article).where(Article.url == art.url))
        if art_res.scalar_one_or_none() is None:
            db.add(
                Article(
                    journalist_id=journalist.id,
                    title=art.title,
                    url=art.url,
                    published_at=art.published_at,
                    summary=art.summary,
                    outlet=journalist.outlet,
                    source_url=art.source_url,
                    scraped_at=art.scraped_at,
                    content_hash=art.content_hash,
                )
            )

    await db.commit()
    refreshed = await db.execute(
        select(Journalist)
        .options(selectinload(Journalist.socials), selectinload(Journalist.articles))
        .where(Journalist.id == journalist.id)
    )
    return refreshed.scalar_one()

