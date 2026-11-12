from __future__ import annotations

import json
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

from app.core.config import get_settings
from app.services.scraper import _EMAIL_RE, _is_usable_email

EXA_SEARCH_URL = "https://api.exa.ai/search"


@dataclass(slots=True)
class ExaArticleHit:
    title: str
    url: str
    author: str
    outlet: str
    highlights: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ExaBioHit:
    email: str | None = None
    email_source_url: str | None = None
    socials: list[tuple[str, str]] = field(default_factory=list)  # (platform, url)


async def _post_exa_search(
    payload: dict[str, object],
    *,
    api_key: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, object]:
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
    }
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(15.0),
        transport=transport,
    ) as client:
        resp = await client.post(EXA_SEARCH_URL, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {}


async def search_articles_with_authors(
    niche_query: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[ExaArticleHit]:
    """Discover niche-relevant articles with verified individual authors via Exa /search."""
    settings = get_settings()
    if not settings.EXA_API_KEY:
        return []

    payload: dict[str, object] = {
        "query": f"latest news and analysis on {niche_query}",
        "type": "auto",
        "systemPrompt": (
            "Include an article only when its page names an individual author. "
            "Omit results whose author cannot be verified; never substitute 'Staff' or the publication name."
        ),
        "outputSchema": {
            "type": "object",
            "properties": {
                "articles": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "author": {"type": "string"},
                            "url": {"type": "string"},
                            "title": {"type": "string"},
                            "outlet": {"type": "string"},
                        },
                        "required": ["author", "url"],
                    },
                }
            },
            "required": ["articles"],
        },
        "contents": {"highlights": True},
    }

    try:
        data = await _post_exa_search(
            payload, api_key=settings.EXA_API_KEY, transport=transport
        )
    except Exception:
        return []

    raw_results = data.get("results")
    result_by_url: dict[str, dict[str, object]] = {}
    if isinstance(raw_results, list):
        for item in raw_results:
            if isinstance(item, dict) and isinstance(item.get("url"), str):
                result_by_url[item["url"]] = item

    hits: list[ExaArticleHit] = []
    seen_urls: set[str] = set()

    output = data.get("output")
    if isinstance(output, dict):
        content = output.get("content")
        parsed_content: dict[str, object] | None = None
        if isinstance(content, dict):
            parsed_content = content
        elif isinstance(content, str):
            try:
                loaded = json.loads(content)
                if isinstance(loaded, dict):
                    parsed_content = loaded
            except json.JSONDecodeError:
                parsed_content = None

        raw_articles = parsed_content.get("articles") if parsed_content else None
        if isinstance(raw_articles, list):
            for art in raw_articles:
                if not isinstance(art, dict):
                    continue
                url = str(art.get("url") or "").strip()
                author = str(art.get("author") or "").strip()
                if not url or not author or url in seen_urls:
                    continue
                meta = result_by_url.get(url, {})
                title = str(art.get("title") or meta.get("title") or "").strip()
                outlet = str(art.get("outlet") or urlparse(url).netloc).strip()
                highlights_raw = meta.get("highlights")
                highlights = (
                    [str(h) for h in highlights_raw]
                    if isinstance(highlights_raw, list)
                    else []
                )
                seen_urls.add(url)
                hits.append(
                    ExaArticleHit(
                        title=title or url,
                        url=url,
                        author=author,
                        outlet=outlet,
                        highlights=highlights,
                    )
                )

    # Fallback to top-level results that already carry an explicit author field
    if not hits and isinstance(raw_results, list):
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            author = str(item.get("author") or "").strip()
            if not url or not author or url in seen_urls:
                continue
            title = str(item.get("title") or url).strip()
            outlet = urlparse(url).netloc
            highlights_raw = item.get("highlights")
            highlights = (
                [str(h) for h in highlights_raw]
                if isinstance(highlights_raw, list)
                else []
            )
            seen_urls.add(url)
            hits.append(
                ExaArticleHit(
                    title=title,
                    url=url,
                    author=author,
                    outlet=outlet,
                    highlights=highlights,
                )
            )

    return hits


async def search_journalist_bio(
    author_name: str,
    outlet: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ExaBioHit:
    """Search for a journalist's public bio, contact email, and social handles via Exa /search."""
    settings = get_settings()
    if not settings.EXA_API_KEY or not author_name.strip():
        return ExaBioHit()

    payload: dict[str, object] = {
        "query": f"{author_name} {outlet} journalist contact email author profile",
        "type": "auto",
        "objective": (
            "Find the journalist's publicly listed contact email, X/Twitter handle, "
            "LinkedIn profile, or personal author bio page."
        ),
        "contents": {"highlights": True},
    }

    try:
        data = await _post_exa_search(
            payload, api_key=settings.EXA_API_KEY, transport=transport
        )
    except Exception:
        return ExaBioHit()

    bio = ExaBioHit()
    seen_socials: set[str] = set()
    raw_results = data.get("results")
    if not isinstance(raw_results, list):
        return bio

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
            highlights = item.get("highlights")
            if isinstance(highlights, list):
                for snippet in highlights:
                    for match in _EMAIL_RE.findall(str(snippet)):
                        if _is_usable_email(match):
                            bio.email = match.lower()
                            bio.email_source_url = url or "exa-search"
                            break
                    if bio.email:
                        break
    return bio


async def search_company_context(
    company_name: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[dict[str, str]]:
    """Retrieve raw company documents (homepage/newsroom) via Exa /search category='company'."""
    settings = get_settings()
    if not settings.EXA_API_KEY or not company_name.strip():
        return []

    payload: dict[str, object] = {
        "query": f"{company_name} official website newsroom press",
        "type": "auto",
        "category": "company",
        "contents": {"highlights": True},
    }

    try:
        data = await _post_exa_search(
            payload, api_key=settings.EXA_API_KEY, transport=transport
        )
    except Exception:
        return []

    raw_results = data.get("results")
    if not isinstance(raw_results, list):
        return []

    out: list[dict[str, str]] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or "").strip()
        if url:
            out.append({"title": title or url, "url": url})
    return out

