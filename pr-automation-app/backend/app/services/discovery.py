import hashlib
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse

import feedparser  # type: ignore[import-untyped]
import httpx
from scrapling.parser import Selector  # type: ignore[import-untyped]
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import Client, Journalist
from app.services.journalists import upsert_scraped_journalist
from app.services.scraper import (
    _EMAIL_RE,
    DomainRateLimiter,
    RobotsChecker,
    ScrapedArticle,
    ScrapedJournalist,
    _is_usable_email,
    extract_journalist_from_html,
    validate_public_http_url,
)

# Public, high-signal publication RSS feeds for tech/AI/business/science PR discovery
PUBLICATION_RSS_FEEDS: list[tuple[str, str]] = [
    ("TechCrunch", "https://techcrunch.com/feed/"),
    ("VentureBeat", "https://venturebeat.com/feed/"),
    ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"),
    ("Wired", "https://www.wired.com/feed/rss"),
    ("MIT Technology Review", "https://www.technologyreview.com/feed/"),
]


def build_discovery_queries(client: Client, topic_override: str = "") -> list[str]:
    """Build search topic queries from a candidate client's expertise/industry."""
    if topic_override.strip():
        return [topic_override.strip()]
    topics: list[str] = []
    if client.industry.strip():
        topics.append(client.industry.strip())
    role = str(client.key_facts.get("role_title", "")).strip()
    if role and role.lower() not in (client.industry or "").lower():
        topics.append(f"{role} {client.industry}".strip())
    if not topics:
        topics.append(client.name)
    return topics[:2]


def _unwrap_news_link(link: str) -> str:
    """Unwrap Bing News redirect URLs to the real publisher URL."""
    parsed = urlparse(link)
    if "bing.com" in parsed.netloc.lower() and parsed.path.startswith("/news/apiclick"):
        qs = parse_qs(parsed.query)
        if "url" in qs and qs["url"]:
            return qs["url"][0]
    return link


_NON_PERSON_MARKERS = (
    "job:",
    "jobs:",
    "hiring",
    "careers",
    "msn",
    "reuters",
    "associated press",
    "timesofindia",
    "et online",
    "editorial",
    "news desk",
    "newsroom",
    "staff",
    "admin",
    "unknown journalist",
    ".com",
    ".org",
    ".net",
    "http",
)


def _is_valid_journalist_name(name: str) -> bool:
    cleaned = name.strip()
    if len(cleaned) < 4 or len(cleaned) > 60:
        return False
    lower = cleaned.lower()
    if any(m in lower for m in _NON_PERSON_MARKERS):
        return False
    if any(ch in cleaned for ch in ("|", "/", ":", "@")):
        return False
    words = [w for w in cleaned.split() if any(c.isalpha() for c in w)]
    return len(words) >= 2


def _extract_rss_email(entry: object) -> str | None:
    detail = getattr(entry, "author_detail", None)
    if isinstance(detail, dict):
        cand = str(detail.get("email", "")).strip().lower()
        if cand and _is_usable_email(cand):
            return cand
    raw_author = str(getattr(entry, "author", ""))
    for m in _EMAIL_RE.findall(raw_author):
        cand = m.lower()
        if _is_usable_email(cand):
            return cand
    for field_name in ("summary", "description"):
        val = str(getattr(entry, field_name, ""))
        for m in _EMAIL_RE.findall(val):
            cand = m.lower()
            if _is_usable_email(cand):
                return cand
    return None


def _find_author_profile_url(html: str, base_url: str) -> str | None:
    sel = Selector(html)
    for css in (
        "a[rel='author']::attr(href)",
        "a[href*='/author/']::attr(href)",
        "a[href*='/journalist/']::attr(href)",
        "a[href*='/staff/']::attr(href)",
    ):
        href = sel.css(css).get()
        if href:
            full = urljoin(base_url, str(href).strip())
            if full.startswith(("http://", "https://")):
                return full
    return None


def _extract_byline_from_html(html: str) -> str | None:
    sel = Selector(html)
    author = (
        sel.css("meta[name='author']::attr(content)").get()
        or sel.css("meta[property='article:author']::attr(content)").get()
        or sel.css("[rel='author']::text").get()
        or sel.css(".author-name::text, .byline__name::text, .byline::text").get()
    )
    if author:
        cleaned = str(author).strip()
        if cleaned and len(cleaned) <= 100 and "http" not in cleaned.lower():
            return cleaned
    return None


async def discover_journalists_for_client(
    db: AsyncSession,
    client_obj: Client,
    *,
    topic_override: str = "",
    max_results: int = 10,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[Journalist]:
    """Bulk-discover journalists from public RSS feeds & article pages for a client."""
    settings = get_settings()
    queries = build_discovery_queries(client_obj, topic_override=topic_override)
    rate_limiter = DomainRateLimiter(delay_seconds=min(float(settings.SCRAPE_DOMAIN_DELAY_SEC), 0.25))
    robots = RobotsChecker(user_agent=settings.SCRAPER_USER_AGENT)

    discovered_candidates: dict[str, ScrapedJournalist] = {}
    now = datetime.now(timezone.utc)

    # Combine major publication RSS feeds + Bing News topic RSS
    feed_sources: list[tuple[str, str]] = PUBLICATION_RSS_FEEDS + [
        (
            "Bing News",
            f"https://www.bing.com/news/search?q={quote_plus(q)}&format=rss",
        )
        for q in queries
    ]
    pool_limit = max_results + 2

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(6.0),
        follow_redirects=True,
        transport=transport,
    ) as http:
        for default_outlet, rss_url in feed_sources:
            if len(discovered_candidates) >= pool_limit:
                break
            try:
                validate_public_http_url(rss_url)
                rss_domain = urlparse(rss_url).netloc.lower()
                await rate_limiter.acquire(rss_domain)
                resp = await http.get(
                    rss_url, headers={"User-Agent": settings.SCRAPER_USER_AGENT}
                )
                if resp.status_code != 200:
                    continue
            except Exception:
                continue

            feed = feedparser.parse(resp.text)
            for entry in feed.entries[: max_results * 2]:
                if len(discovered_candidates) >= pool_limit:
                    break

                art_title = str(getattr(entry, "title", "")).strip()
                raw_link = str(getattr(entry, "link", "")).strip()
                if not art_title or not raw_link:
                    continue
                art_link = _unwrap_news_link(raw_link)

                source_meta = getattr(entry, "source", None)
                outlet = str(
                    getattr(source_meta, "title", "")
                    or (default_outlet if default_outlet != "Bing News" else urlparse(art_link).netloc)
                ).strip()
                author_name = str(getattr(entry, "author", "")).strip()
                rss_email = _extract_rss_email(entry)
                summary = re.sub(r"<[^>]+>", " ", str(getattr(entry, "summary", ""))).strip()[:400]

                # Enrich from article page (respecting robots.txt) for byline, email, and socials
                enriched: ScrapedJournalist | None = None
                try:
                    clean_art_url = validate_public_http_url(art_link)
                    art_domain = urlparse(clean_art_url).netloc.lower()
                    await rate_limiter.acquire(art_domain)
                    if await robots.can_fetch(http, clean_art_url):
                        art_resp = await http.get(
                            clean_art_url, headers={"User-Agent": settings.SCRAPER_USER_AGENT}
                        )
                        if art_resp.status_code == 200:
                            byline = _extract_byline_from_html(art_resp.text)
                            if byline:
                                author_name = byline
                            parsed_page = extract_journalist_from_html(
                                art_resp.text,
                                source_url=str(art_resp.url),
                                outlet=outlet,
                                beat=queries[0],
                            )
                            if author_name:
                                parsed_page.canonical_name = author_name
                            # Fallback to author profile page if article page has no email
                            if parsed_page.email is None:
                                author_url = _find_author_profile_url(art_resp.text, str(art_resp.url))
                                if author_url:
                                    try:
                                        clean_auth_url = validate_public_http_url(author_url)
                                        auth_domain = urlparse(clean_auth_url).netloc.lower()
                                        await rate_limiter.acquire(auth_domain)
                                        if await robots.can_fetch(http, clean_auth_url):
                                            auth_resp = await http.get(
                                                clean_auth_url,
                                                headers={"User-Agent": settings.SCRAPER_USER_AGENT},
                                            )
                                            if auth_resp.status_code == 200:
                                                auth_page = extract_journalist_from_html(
                                                    auth_resp.text,
                                                    source_url=str(auth_resp.url),
                                                    outlet=outlet,
                                                    beat=queries[0],
                                                )
                                                if auth_page.email:
                                                    parsed_page.email = auth_page.email
                                                    parsed_page.email_source_url = auth_page.email_source_url
                                                    parsed_page.email_scraped_at = auth_page.email_scraped_at
                                                if not parsed_page.socials and auth_page.socials:
                                                    parsed_page.socials = auth_page.socials
                                    except Exception:
                                        pass
                            enriched = parsed_page
                except Exception:
                    enriched = None

                final_name = (
                    author_name
                    or (enriched.canonical_name if enriched else "")
                ).strip()
                if not _is_valid_journalist_name(final_name):
                    continue

                dedupe_key = f"{final_name.lower()}|{outlet.lower()}"
                article_obj = ScrapedArticle(
                    title=art_title,
                    url=art_link,
                    published_at=None,
                    summary=summary,
                    source_url=rss_url,
                    scraped_at=now,
                    content_hash=hashlib.sha256(f"{art_title}|{art_link}".encode()).hexdigest(),
                )

                if dedupe_key not in discovered_candidates:
                    if enriched is not None:
                        if enriched.email is None and rss_email is not None:
                            enriched.email = rss_email
                            enriched.email_source_url = rss_url
                            enriched.email_scraped_at = now
                        if not any(a.url == art_link for a in enriched.articles):
                            enriched.articles.insert(0, article_obj)
                        discovered_candidates[dedupe_key] = enriched
                    else:
                        discovered_candidates[dedupe_key] = ScrapedJournalist(
                            canonical_name=final_name,
                            outlet=outlet,
                            beat=queries[0],
                            email=rss_email,
                            email_source_url=rss_url if rss_email else None,
                            email_scraped_at=now if rss_email else None,
                            profile_source_url=art_link,
                            profile_scraped_at=now,
                            confidence_score=0.75 if rss_email else 0.6,
                            socials=[],
                            articles=[article_obj],
                        )

    ranked = sorted(
        discovered_candidates.values(),
        key=lambda c: (c.email is not None, c.confidence_score, len(c.articles)),
        reverse=True,
    )[:max_results]

    persisted: list[Journalist] = []
    for candidate in ranked:
        j = await upsert_scraped_journalist(db, candidate)
        persisted.append(j)
    return persisted
