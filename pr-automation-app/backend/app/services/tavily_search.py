from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from tavily import AsyncTavilyClient  # type: ignore[import-untyped]

from app.core.config import get_settings
from app.services.scraper import _EMAIL_RE, _is_usable_email

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
TAVILY_EXTRACT_URL = "https://api.tavily.com/extract"


@dataclass(slots=True)
class TavilyArticleHit:
    title: str
    url: str
    outlet: str
    content: str


@dataclass(slots=True)
class TavilyBioHit:
    email: str | None = None
    email_source_url: str | None = None
    socials: list[tuple[str, str]] | None = None


async def _tavily_search(
    *,
    query: str,
    topic: str = "general",
    search_depth: str = "basic",
    max_results: int = 8,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, object]:
    settings = get_settings()
    if not settings.TAVILY_API_KEY:
        return {}

    if transport is None:
        client = AsyncTavilyClient(api_key=settings.TAVILY_API_KEY)
        res = await client.search(
            query=query,
            topic=topic,
            search_depth=search_depth,
            max_results=max_results,
        )
        return res if isinstance(res, dict) else {}

    payload = {
        "api_key": settings.TAVILY_API_KEY,
        "query": query,
        "topic": topic,
        "search_depth": search_depth,
        "max_results": max_results,
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0), transport=transport) as http:
        resp = await http.post(TAVILY_SEARCH_URL, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {}


async def search_tavily_articles(
    niche_query: str,
    *,
    max_results: int = 8,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[TavilyArticleHit]:
    """Search recent niche news articles using Tavily."""
    try:
        data = await _tavily_search(
            query=niche_query,
            topic="news",
            search_depth="basic",
            max_results=max_results,
            transport=transport,
        )
    except Exception:
        return []

    raw_results = data.get("results")
    if not isinstance(raw_results, list):
        return []

    hits: list[TavilyArticleHit] = []
    seen_urls: set[str] = set()
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or "").strip()
        content = str(item.get("content") or "").strip()
        if not url or not title or url in seen_urls:
            continue
        seen_urls.add(url)
        outlet = urlparse(url).netloc.lower()
        hits.append(
            TavilyArticleHit(
                title=title,
                url=url,
                outlet=outlet,
                content=content[:400],
            )
        )
    return hits


async def search_tavily_bio(
    author_name: str,
    outlet: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> TavilyBioHit:
    """Search for a journalist's public bio email and social links using Tavily."""
    if not author_name.strip():
        return TavilyBioHit(socials=[])

    try:
        data = await _tavily_search(
            query=f'"{author_name}" "{outlet}" email OR contact OR site:x.com OR site:linkedin.com',
            topic="general",
            search_depth="basic",
            max_results=5,
            transport=transport,
        )
    except Exception:
        return TavilyBioHit(socials=[])

    raw_results = data.get("results")
    if not isinstance(raw_results, list):
        return TavilyBioHit(socials=[])

    bio = TavilyBioHit(socials=[])
    seen_socials: set[str] = set()
    assert bio.socials is not None

    for item in raw_results:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        lower_url = url.lower()
        if "linkedin.com/in/" in lower_url and url not in seen_socials:
            seen_socials.add(url)
            bio.socials.append(("linkedin", url))
        elif ("x.com/" in lower_url or "twitter.com/" in lower_url) and url not in seen_socials:
            seen_socials.add(url)
            bio.socials.append(("x", url))

        if bio.email is None:
            content = str(item.get("content") or "")
            for match in _EMAIL_RE.findall(content):
                if _is_usable_email(match):
                    bio.email = match.lower()
                    bio.email_source_url = url or "tavily-search"
                    break
    return bio


async def extract_tavily_urls(
    urls: list[str],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, str]:
    """Extract clean page content for known URLs using Tavily Extract."""
    settings = get_settings()
    if not settings.TAVILY_API_KEY or not urls:
        return {}

    try:
        if transport is None:
            client = AsyncTavilyClient(api_key=settings.TAVILY_API_KEY)
            data = await client.extract(urls=urls[:5])
        else:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(15.0), transport=transport
            ) as http:
                resp = await http.post(
                    TAVILY_EXTRACT_URL,
                    json={"api_key": settings.TAVILY_API_KEY, "urls": urls[:5]},
                )
                resp.raise_for_status()
                data = resp.json()
    except Exception:
        return {}

    if not isinstance(data, dict):
        return {}
    raw_results = data.get("results")
    if not isinstance(raw_results, list):
        return {}

    extracted: dict[str, str] = {}
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        u = str(item.get("url") or "").strip()
        raw_content = str(item.get("raw_content") or item.get("content") or "").strip()
        if u and raw_content:
            extracted[u] = raw_content
    return extracted

