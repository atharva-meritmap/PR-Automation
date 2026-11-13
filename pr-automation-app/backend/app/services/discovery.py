import asyncio
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
from app.services.email_verifier import resolve_journalist_email, verify_email_address
from app.services.exa_search import (
    search_articles_with_authors,
    search_journalist_bio,
)
from app.services.journalists import upsert_scraped_journalist
from app.services.scraper import (
    _EMAIL_RE,
    DomainRateLimiter,
    RobotsChecker,
    ScrapedArticle,
    ScrapedJournalist,
    ScrapedSocial,
    _is_usable_email,
    extract_journalist_from_html,
    smart_fetch_html,
    validate_public_http_url,
)
from app.services.searxng_search import dork_journalist_bio_searxng
from app.services.tavily_search import (
    search_tavily_articles,
    search_tavily_bio,
)

# Public, high-signal publication & trade RSS feeds for tech/AI/biotech/fintech/science PR discovery
PUBLICATION_RSS_FEEDS: list[tuple[str, str]] = [
    ("TechCrunch", "https://techcrunch.com/feed/"),
    ("VentureBeat", "https://venturebeat.com/feed/"),
    ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"),
    ("Wired", "https://www.wired.com/feed/rss"),
    ("MIT Technology Review", "https://www.technologyreview.com/feed/"),
    ("IEEE Spectrum", "https://spectrum.ieee.org/feeds/feed.rss"),
    ("Finextra", "https://www.finextra.com/rss/headlines.aspx"),
    ("Fierce Biotech", "https://www.fiercebiotech.com/rss/xml"),
]


async def fetch_bluesky_journalist_candidates(
    http: httpx.AsyncClient,
    topic_query: str,
    *,
    now: datetime,
    limit: int = 8,
) -> list[tuple[str, ScrapedJournalist]]:
    """Discover writers/podcasters/reporters posting about the candidate's niche on Bluesky."""
    try:
        resp = await http.get(
            "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts",
            params={"q": f"{topic_query} journalist OR newsletter OR podcast", "limit": limit},
        )
        if resp.status_code != 200:
            return []
        posts = resp.json().get("posts") or []
    except Exception:
        return []

    found: list[tuple[str, ScrapedJournalist]] = []
    for post in posts:
        if not isinstance(post, dict):
            continue
        author = post.get("author") or {}
        record = post.get("record") or {}
        handle = str(author.get("handle") or "").strip()
        display_name = str(author.get("displayName") or "").strip()
        text = str(record.get("text") or "").strip()
        if not handle or not _is_valid_journalist_name(display_name):
            continue

        profile_url = f"https://bsky.app/profile/{handle}"
        email: str | None = None
        for match in _EMAIL_RE.findall(text):
            if _is_usable_email(match):
                email = match.lower()
                break

        uri = str(post.get("uri") or "")
        post_id = uri.rsplit("/", 1)[-1] if "/" in uri else "post"
        post_url = f"{profile_url}/post/{post_id}"
        article_obj = ScrapedArticle(
            title=text[:140] or f"Bluesky post by {display_name}",
            url=post_url,
            published_at=None,
            summary=text[:400],
            source_url=profile_url,
            scraped_at=now,
            content_hash=hashlib.sha256(post_url.encode()).hexdigest(),
        )
        candidate = ScrapedJournalist(
            canonical_name=display_name,
            outlet="Bluesky",
            beat=topic_query,
            email=email,
            email_source_url=post_url if email else None,
            email_scraped_at=now if email else None,
            profile_source_url=profile_url,
            profile_scraped_at=now,
            confidence_score=0.7 if email else 0.55,
            socials=[
                ScrapedSocial(
                    platform="bluesky",
                    handle_or_url=profile_url,
                    source_url=profile_url,
                    scraped_at=now,
                )
            ],
            articles=[article_obj],
        )
        found.append((f"{display_name.lower()}|bluesky", candidate))
    return found


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

    # Combine major publication/trade RSS feeds + Bing News + Substack/Podcast topic RSS
    feed_sources: list[tuple[str, str]] = (
        PUBLICATION_RSS_FEEDS
        + [
            (
                "Bing News",
                f"https://www.bing.com/news/search?q={quote_plus(q)}&format=rss",
            )
            for q in queries
        ]
        + [
            (
                "Substack",
                f"https://www.bing.com/news/search?q={quote_plus(f'site:substack.com {queries[0]}')}&format=rss",
            ),
            (
                "Podcast",
                f"https://www.bing.com/news/search?q={quote_plus(f'{queries[0]} podcast interview')}&format=rss",
            ),
        ]
    )
    pool_limit = max_results + 2
    allow_stealth = transport is None
    sem = asyncio.Semaphore(6)

    async def _enrich_article_candidate(
        http: httpx.AsyncClient,
        *,
        art_title: str,
        art_link: str,
        outlet: str,
        author_name: str,
        rss_email: str | None,
        summary: str,
        source_url: str,
    ) -> tuple[str, ScrapedJournalist] | None:
        enriched: ScrapedJournalist | None = None
        art_domain = ""
        async with sem:
            try:
                clean_art_url = validate_public_http_url(art_link)
                art_domain = urlparse(clean_art_url).netloc.lower()
                await rate_limiter.acquire(art_domain)
                if await robots.can_fetch(http, clean_art_url):
                    art_status, art_final_url, art_html = await smart_fetch_html(
                        http,
                        clean_art_url,
                        user_agent=settings.SCRAPER_USER_AGENT,
                        allow_stealth_fallback=allow_stealth,
                    )
                    if art_status == 200 and art_html:
                        byline = _extract_byline_from_html(art_html)
                        if byline:
                            author_name = byline
                        parsed_page = extract_journalist_from_html(
                            art_html,
                            source_url=art_final_url,
                            outlet=outlet,
                            beat=queries[0],
                        )
                        if author_name:
                            parsed_page.canonical_name = author_name
                        if parsed_page.email is None:
                            author_url = _find_author_profile_url(art_html, art_final_url)
                            if author_url:
                                try:
                                    clean_auth_url = validate_public_http_url(author_url)
                                    auth_domain = urlparse(clean_auth_url).netloc.lower()
                                    await rate_limiter.acquire(auth_domain)
                                    if await robots.can_fetch(http, clean_auth_url):
                                        auth_status, auth_final_url, auth_html = await smart_fetch_html(
                                            http,
                                            clean_auth_url,
                                            user_agent=settings.SCRAPER_USER_AGENT,
                                            allow_stealth_fallback=allow_stealth,
                                        )
                                        if auth_status == 200 and auth_html:
                                            auth_page = extract_journalist_from_html(
                                                auth_html,
                                                source_url=auth_final_url,
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
            return None

        dedupe_key = f"{final_name.lower()}|{outlet.lower()}"
        article_obj = ScrapedArticle(
            title=art_title,
            url=art_link,
            published_at=None,
            summary=summary,
            source_url=source_url,
            scraped_at=now,
            content_hash=hashlib.sha256(f"{art_title}|{art_link}".encode()).hexdigest(),
        )

        if enriched is not None:
            if enriched.email is None and rss_email is not None:
                enriched.email = rss_email
                enriched.email_source_url = source_url
                enriched.email_scraped_at = now
            if not any(a.url == art_link for a in enriched.articles):
                enriched.articles.insert(0, article_obj)
            candidate = enriched
        else:
            candidate = ScrapedJournalist(
                canonical_name=final_name,
                outlet=outlet,
                beat=queries[0],
                email=rss_email,
                email_source_url=source_url if rss_email else None,
                email_scraped_at=now if rss_email else None,
                profile_source_url=art_link,
                profile_scraped_at=now,
                confidence_score=0.75 if rss_email else 0.6,
                socials=[],
                articles=[article_obj],
            )

        def _merge_bio_fields(
            email: str | None,
            email_source_url: str | None,
            socials: list[tuple[str, str]] | None,
        ) -> None:
            if candidate.email is None and email:
                candidate.email = email
                candidate.email_source_url = email_source_url
                candidate.email_scraped_at = now
            if socials:
                existing_urls = {s.handle_or_url for s in candidate.socials}
                for platform, social_url in socials:
                    if social_url not in existing_urls:
                        existing_urls.add(social_url)
                        candidate.socials.append(
                            ScrapedSocial(
                                platform=platform,
                                handle_or_url=social_url,
                                source_url=social_url,
                                scraped_at=now,
                            )
                        )

        # Step 2a: SearXNG OSINT boolean dorking when configured
        if settings.SEARXNG_BASE_URL and (candidate.email is None or not candidate.socials):
            sx_bio = await dork_journalist_bio_searxng(
                final_name,
                outlet,
                transport=transport,
            )
            _merge_bio_fields(sx_bio.email, sx_bio.email_source_url, sx_bio.socials)

        # Step 2b: Exa bio & social search when email or socials are still missing
        if settings.EXA_API_KEY and (candidate.email is None or not candidate.socials):
            bio_hit = await search_journalist_bio(
                final_name,
                outlet,
                transport=transport,
            )
            _merge_bio_fields(bio_hit.email, bio_hit.email_source_url, bio_hit.socials)

        # Step 2c: Tavily bio & social search when email or socials are still missing
        if settings.TAVILY_API_KEY and (candidate.email is None or not candidate.socials):
            t_bio = await search_tavily_bio(
                final_name,
                outlet,
                transport=transport,
            )
            _merge_bio_fields(t_bio.email, t_bio.email_source_url, t_bio.socials)

        # Hunter -> Prospeo -> MX+Verifier pattern fallback (never fabricates unverified emails)
        if candidate.email is None and art_domain:
            verified_email, v_status, v_source = await resolve_journalist_email(
                final_name,
                art_domain,
                transport=transport,
            )
            if verified_email and v_status == "verified":
                candidate.email = verified_email
                candidate.email_status = "verified"
                candidate.email_source_url = f"{v_source}://{art_domain}"
                candidate.email_scraped_at = now
        elif candidate.email is not None and candidate.email_status == "unverified":
            try:
                verif = await verify_email_address(candidate.email, transport=transport)
                candidate.email_status = verif.status
            except Exception:
                pass

        return dedupe_key, candidate

    async def _enrich_entry(
        http: httpx.AsyncClient,
        default_outlet: str,
        rss_url: str,
        entry: object,
    ) -> tuple[str, ScrapedJournalist] | None:
        art_title = str(getattr(entry, "title", "")).strip()
        raw_link = str(getattr(entry, "link", "")).strip()
        if not art_title or not raw_link:
            return None
        art_link = _unwrap_news_link(raw_link)

        source_meta = getattr(entry, "source", None)
        outlet = str(
            getattr(source_meta, "title", "")
            or (default_outlet if default_outlet != "Bing News" else urlparse(art_link).netloc)
        ).strip()
        author_name = str(getattr(entry, "author", "")).strip()
        rss_email = _extract_rss_email(entry)
        summary = re.sub(r"<[^>]+>", " ", str(getattr(entry, "summary", ""))).strip()[:400]
        return await _enrich_article_candidate(
            http,
            art_title=art_title,
            art_link=art_link,
            outlet=outlet,
            author_name=author_name,
            rss_email=rss_email,
            summary=summary,
            source_url=rss_url,
        )

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(6.0),
        follow_redirects=True,
        transport=transport,
    ) as http:
        if settings.EXA_API_KEY:
            exa_hits = await search_articles_with_authors(
                queries[0],
                transport=transport,
            )
            if exa_hits:
                exa_results = await asyncio.gather(
                    *(
                        _enrich_article_candidate(
                            http,
                            art_title=hit.title,
                            art_link=hit.url,
                            outlet=hit.outlet,
                            author_name=hit.author,
                            rss_email=None,
                            summary=" ".join(hit.highlights)[:400],
                            source_url="https://api.exa.ai/search",
                        )
                        for hit in exa_hits[: max_results * 2]
                    ),
                    return_exceptions=True,
                )
                for item in exa_results:
                    if isinstance(item, BaseException) or item is None:
                        continue
                    dedupe_key, candidate = item
                    if dedupe_key not in discovered_candidates:
                        discovered_candidates[dedupe_key] = candidate
                        if len(discovered_candidates) >= pool_limit:
                            break

        if settings.TAVILY_API_KEY and len(discovered_candidates) < pool_limit:
            tavily_hits = await search_tavily_articles(
                queries[0],
                max_results=max_results * 2,
                transport=transport,
            )
            if tavily_hits:
                tavily_results = await asyncio.gather(
                    *(
                        _enrich_article_candidate(
                            http,
                            art_title=hit.title,
                            art_link=hit.url,
                            outlet=hit.outlet,
                            author_name="",
                            rss_email=None,
                            summary=hit.content,
                            source_url="https://api.tavily.com/search",
                        )
                        for hit in tavily_hits
                    ),
                    return_exceptions=True,
                )
                for item in tavily_results:
                    if isinstance(item, BaseException) or item is None:
                        continue
                    dedupe_key, candidate = item
                    if dedupe_key not in discovered_candidates:
                        discovered_candidates[dedupe_key] = candidate
                        if len(discovered_candidates) >= pool_limit:
                            break

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
            entries = feed.entries[: max_results * 2]
            results = await asyncio.gather(
                *(_enrich_entry(http, default_outlet, rss_url, entry) for entry in entries),
                return_exceptions=True,
            )
            for item in results:
                if isinstance(item, BaseException) or item is None:
                    continue
                dedupe_key, candidate = item
                if dedupe_key not in discovered_candidates:
                    discovered_candidates[dedupe_key] = candidate
                    if len(discovered_candidates) >= pool_limit:
                        break

        if len(discovered_candidates) < pool_limit:
            bsky_hits = await fetch_bluesky_journalist_candidates(
                http, queries[0], now=now, limit=max_results
            )
            for dedupe_key, candidate in bsky_hits:
                if dedupe_key not in discovered_candidates:
                    discovered_candidates[dedupe_key] = candidate
                    if len(discovered_candidates) >= pool_limit:
                        break

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
