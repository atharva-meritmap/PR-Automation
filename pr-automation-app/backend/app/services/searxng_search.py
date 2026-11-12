from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from app.core.config import get_settings
from app.services.scraper import _EMAIL_RE, _is_usable_email


@dataclass(slots=True)
class SearxngBioHit:
    email: str | None = None
    email_source_url: str | None = None
    socials: list[tuple[str, str]] = field(default_factory=list)  # (platform, url)


async def dork_journalist_bio_searxng(
    author_name: str,
    outlet: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> SearxngBioHit:
    """Run exact boolean OSINT dorking against a configured SearXNG instance for bio/email/socials."""
    settings = get_settings()
    base_url = settings.SEARXNG_BASE_URL.strip().rstrip("/")
    if not base_url or not author_name.strip():
        return SearxngBioHit()

    query = (
        f'"{author_name}" "{outlet}" '
        '(site:muckrack.com OR site:x.com OR site:linkedin.com OR "reach me at" OR "email" OR "tips")'
    )

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(12.0),
            transport=transport,
        ) as client:
            resp = await client.get(
                f"{base_url}/search",
                params={"q": query, "format": "json", "language": "en"},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return SearxngBioHit()

    if not isinstance(data, dict):
        return SearxngBioHit()

    raw_results = data.get("results")
    if not isinstance(raw_results, list):
        return SearxngBioHit()

    bio = SearxngBioHit()
    seen_socials: set[str] = set()

    for item in raw_results:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        lower_url = url.lower()

        platform: str | None = None
        if "linkedin.com/in/" in lower_url:
            platform = "linkedin"
        elif "x.com/" in lower_url or "twitter.com/" in lower_url:
            platform = "x"
        elif "bsky.app/profile/" in lower_url:
            platform = "bluesky"
        elif "muckrack.com/" in lower_url:
            platform = "muckrack"

        if platform and url not in seen_socials:
            seen_socials.add(url)
            bio.socials.append((platform, url))

        if bio.email is None:
            text_blob = f"{item.get('title') or ''} {item.get('content') or ''}"
            for match in _EMAIL_RE.findall(text_blob):
                if _is_usable_email(match):
                    bio.email = match.lower()
                    bio.email_source_url = url or base_url
                    break

    return bio
