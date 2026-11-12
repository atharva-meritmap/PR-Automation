import hashlib
import html as html_lib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus, urlparse

import feedparser  # type: ignore[import-untyped]
import httpx
from scrapling.parser import Selector  # type: ignore[import-untyped]

from app.core.config import get_settings
from app.db.models import Client, PrOpportunity
from app.services.decision import HeuristicDecisionClassifier
from app.services.discovery import _unwrap_news_link
from app.services.llm import generate_openrouter_pitch
from app.services.scraper import validate_public_http_url

QWOTED_BASE = "https://app.qwoted.com"
_REACT_PROPS_RE = re.compile(
    r'data-react-class="source_requests/top_level_search"\s+data-react-props="([^"]+)"'
)


class QwotedAuthError(Exception):
    """Raised when Qwoted login or session validation fails."""


@dataclass
class DiscoveredOpportunity:
    source_platform: str
    external_id: str
    title: str
    summary: str
    publication: str
    journalist_name: str
    deadline: str
    share_url: str
    hashtags: list[str]


async def login_qwoted(
    email: str,
    password: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, str]:
    """Authenticate with Qwoted and return session cookies."""
    settings = get_settings()
    headers = {"User-Agent": settings.SCRAPER_USER_AGENT}
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(12.0), follow_redirects=True, transport=transport
    ) as client:
        sign_in = await client.get(f"{QWOTED_BASE}/users/sign_in", headers=headers)
        if sign_in.status_code != 200:
            raise QwotedAuthError(f"Qwoted sign-in page returned HTTP {sign_in.status_code}")

        sel = Selector(sign_in.text)
        csrf_token = sel.css("input[name='authenticity_token']::attr(value)").get()
        if not csrf_token:
            raise QwotedAuthError("Could not locate CSRF authenticity_token on Qwoted sign-in page")

        resp = await client.post(
            f"{QWOTED_BASE}/users/sign_in",
            data={
                "authenticity_token": str(csrf_token),
                "user[email]": email,
                "user[password]": password,
                "user[remember_me]": "1",
            },
            headers=headers,
        )
        if "/users/sign_in" in str(resp.url):
            raise QwotedAuthError("Invalid Qwoted email or password")

        cookies = {k: v for k, v in client.cookies.items()}
        if not cookies:
            raise QwotedAuthError("Qwoted login succeeded but no session cookies were issued")
        return cookies


async def fetch_algolia_credentials(
    cookies: dict[str, str],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, str] | None:
    """Extract Qwoted's public search-only Algolia credentials from /source_requests."""
    settings = get_settings()
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(12.0),
        follow_redirects=True,
        cookies=cookies,
        transport=transport,
    ) as client:
        resp = await client.get(
            f"{QWOTED_BASE}/source_requests",
            headers={"User-Agent": settings.SCRAPER_USER_AGENT},
        )
        if resp.status_code != 200 or "/users/sign_in" in str(resp.url):
            return None
        match = _REACT_PROPS_RE.search(resp.text)
        if not match:
            return None
        props = json.loads(html_lib.unescape(match.group(1)))
        app_id = str(props.get("algoliaAppId") or props.get("algolia_app_id") or "")
        api_key = str(props.get("algoliaSearchKey") or props.get("algolia_api_key") or "")
        index_name = str(
            props.get("algoliaIndexName")
            or props.get("algolia_index_name")
            or "SourceRequest_production"
        )
        if not app_id or not api_key:
            return None
        return {"app_id": app_id, "api_key": api_key, "index_name": index_name}


async def search_qwoted_opportunities(
    cookies: dict[str, str],
    query: str,
    *,
    max_hits: int = 15,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[DiscoveredOpportunity]:
    """Query Qwoted's Algolia index for open journalist source requests."""
    creds = await fetch_algolia_credentials(cookies, transport=transport)
    if creds is None:
        raise QwotedAuthError("Qwoted session expired; please reconnect Qwoted.")

    algolia_url = (
        f"https://{creds['app_id']}-dsn.algolia.net/1/indexes/{creds['index_name']}/query"
    )
    async with httpx.AsyncClient(timeout=httpx.Timeout(12.0), transport=transport) as client:
        resp = await client.post(
            algolia_url,
            headers={
                "X-Algolia-Application-Id": creds["app_id"],
                "X-Algolia-API-Key": creds["api_key"],
                "Content-Type": "application/json",
            },
            json={"query": query, "hitsPerPage": min(max_hits, 30)},
        )
        if resp.status_code != 200:
            return []
        hits = resp.json().get("hits", [])

    opportunities: list[DiscoveredOpportunity] = []
    for hit in hits:
        req_id = str(hit.get("objectID") or hit.get("id") or hit.get("source_request_id") or "")
        title = str(hit.get("name") or hit.get("title") or "").strip()
        if not req_id or not title:
            continue
        summary = re.sub(r"<[^>]+>", " ", str(hit.get("details") or hit.get("summary") or "")).strip()
        pub = str(hit.get("publication_name") or hit.get("outlet") or "Qwoted Publication").strip()
        reporter = str(hit.get("reporter_name") or hit.get("user_name") or "").strip()
        deadline = str(hit.get("deadline") or hit.get("expires_at") or "").strip()
        share_url = str(hit.get("share_url") or f"{QWOTED_BASE}/source_requests/{req_id}")
        hashtags = [str(t) for t in hit.get("hashtags", []) if t]
        opportunities.append(
            DiscoveredOpportunity(
                source_platform="qwoted",
                external_id=req_id,
                title=title[:500],
                summary=summary[:1200],
                publication=pub[:255],
                journalist_name=reporter[:255],
                deadline=deadline[:120],
                share_url=share_url[:1024],
                hashtags=hashtags[:10],
            )
        )
    return opportunities


async def discover_public_opportunities(
    client_obj: Client,
    *,
    topic_override: str = "",
    max_results: int = 10,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[DiscoveredOpportunity]:
    """Discover public journalist callouts, expert commentary requests, and CFPs."""
    settings = get_settings()
    topic = topic_override.strip() or client_obj.industry.strip() or client_obj.name
    queries = [
        f"seeking speakers {topic}",
        f"call for speakers {topic}",
        f"seeking experts {topic}",
        "seeking speakers tech conference",
    ]
    opportunities: list[DiscoveredOpportunity] = []
    seen_urls: set[str] = set()

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(8.0), follow_redirects=True, transport=transport
    ) as http:
        for q in queries:
            if len(opportunities) >= max_results:
                break
            rss_url = f"https://www.bing.com/news/search?q={quote_plus(q)}&format=rss"
            try:
                validate_public_http_url(rss_url)
                resp = await http.get(
                    rss_url, headers={"User-Agent": settings.SCRAPER_USER_AGENT}
                )
                if resp.status_code != 200:
                    continue
            except Exception:
                continue

            feed = feedparser.parse(resp.text)
            for entry in feed.entries[: max_results * 2]:
                if len(opportunities) >= max_results:
                    break
                title = str(getattr(entry, "title", "")).strip()
                raw_link = str(getattr(entry, "link", "")).strip()
                if not title or not raw_link:
                    continue
                link = _unwrap_news_link(raw_link)
                if link in seen_urls:
                    continue
                seen_urls.add(link)

                source_meta = getattr(entry, "source", None)
                pub = str(getattr(source_meta, "title", "") or urlparse(link).netloc).strip()
                summary = re.sub(
                    r"<[^>]+>", " ", str(getattr(entry, "summary", ""))
                ).strip()[:1000]
                ext_id = hashlib.sha256(link.encode()).hexdigest()[:24]
                opportunities.append(
                    DiscoveredOpportunity(
                        source_platform="public_callout",
                        external_id=ext_id,
                        title=title[:500],
                        summary=summary,
                        publication=pub[:255],
                        journalist_name=str(getattr(entry, "author", "")).strip()[:255],
                        deadline=str(getattr(entry, "published", "")).strip()[:120],
                        share_url=link[:1024],
                        hashtags=[topic],
                    )
                )

        # Supplement with recent public CFP / speaking opportunities from Dev.to & HN
        if len(opportunities) < max_results:
            try:
                dev_resp = await http.get(
                    "https://dev.to/api/articles?tag=cfp&per_page=8",
                    headers={"User-Agent": settings.SCRAPER_USER_AGENT},
                )
                if dev_resp.status_code == 200:
                    for item in dev_resp.json():
                        if len(opportunities) >= max_results:
                            break
                        link = str(item.get("url") or "").strip()
                        title = str(item.get("title") or "").strip()
                        if not link or not title or link in seen_urls:
                            continue
                        seen_urls.add(link)
                        ext_id = hashlib.sha256(link.encode()).hexdigest()[:24]
                        opportunities.append(
                            DiscoveredOpportunity(
                                source_platform="public_callout",
                                external_id=ext_id,
                                title=title[:500],
                                summary=str(item.get("description") or "").strip()[:1000],
                                publication="DEV Community (CFP)",
                                journalist_name=str((item.get("user") or {}).get("name") or "").strip()[:255],
                                deadline=str(item.get("published_at") or "")[:10],
                                share_url=link[:1024],
                                hashtags=list(item.get("tag_list") or [topic])[:6],
                            )
                        )
            except Exception:
                pass

    return opportunities


def draft_candidate_pitch(client_obj: Client, opp: PrOpportunity) -> str:
    """Draft a grounded, non-fabricated pitch for a candidate using only approved facts."""
    role = str(client_obj.key_facts.get("role_title", "")).strip()
    company = str(client_obj.key_facts.get("company", "")).strip()
    role_line = f"{role} at {company}" if role and company else (role or company or client_obj.industry)
    greeting = f"Hi {opp.journalist_name}," if opp.journalist_name else "Hi there,"
    facts_snippet = client_obj.brand_voice.strip() or f"Deep expertise in {client_obj.industry}."

    return (
        f"{greeting}\n\n"
        f"I saw your request on {opp.publication} regarding \"{opp.title}\" and would love to offer "
        f"an expert perspective from {client_obj.name} ({role_line}).\n\n"
        f"Relevant background (verified):\n{facts_snippet}\n\n"
        f"Happy to share concise, quotable insights or jump on a quick 10-minute call before your deadline.\n\n"
        f"Best regards,\n"
        f"PR Team on behalf of {client_obj.name}"
    )


async def draft_candidate_pitch_async(client_obj: Client, opp: PrOpportunity) -> str:
    """Draft a pitch via OpenRouter free LLM when configured, falling back to template."""
    role = str(client_obj.key_facts.get("role_title", "")).strip()
    company = str(client_obj.key_facts.get("company", "")).strip()
    role_line = f"{role} at {company}" if role and company else (role or company or client_obj.industry)
    approved_facts = client_obj.brand_voice.strip() or f"Expertise in {client_obj.industry}."

    llm_pitch = await generate_openrouter_pitch(
        client_name=client_obj.name,
        role_line=role_line,
        industry=client_obj.industry,
        approved_facts=approved_facts,
        target_title=opp.title,
        target_publication=opp.publication,
        journalist_name=opp.journalist_name,
        target_summary=opp.summary,
    )
    return llm_pitch or draft_candidate_pitch(client_obj, opp)


async def submit_qwoted_pitch(
    cookies: dict[str, str],
    *,
    source_request_id: str,
    pitch_text: str,
    confirm_live_submit: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[str, str]:
    """Submit a pitch to Qwoted (always dry_run unless live submit is explicitly confirmed)."""
    settings = get_settings()
    if settings.MAIL_PROVIDER == "dry_run" or not confirm_live_submit:
        return (
            "dry_run",
            f"Dry-run validated pitch ({len(pitch_text)} chars) for Qwoted request {source_request_id}.",
        )

    headers = {"User-Agent": settings.SCRAPER_USER_AGENT}
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(15.0), cookies=cookies, transport=transport
    ) as client:
        # 1. Discover user's pitchable Source entity
        ent_resp = await client.get(f"{QWOTED_BASE}/api/internal/pitchable_entities", headers=headers)
        if ent_resp.status_code != 200:
            raise QwotedAuthError("Could not fetch pitchable Source entity from Qwoted")
        entities = ent_resp.json()
        entity = entities[0] if isinstance(entities, list) and entities else None
        if not entity:
            raise QwotedAuthError("No pitchable Source profile found in your Qwoted account")

        entity_id = str(entity.get("id"))
        entity_type = str(entity.get("type") or "Source")

        # 2. Create draft
        draft_resp = await client.post(
            f"{QWOTED_BASE}/api/internal/pitches",
            data={"pitch[draft]": "true", "pitch[source_request_id]": source_request_id},
            headers=headers,
        )
        if draft_resp.status_code not in (200, 201):
            raise QwotedAuthError(f"Qwoted draft creation failed (HTTP {draft_resp.status_code})")
        pitch_id = str(draft_resp.json()["id"])

        # 3. Autosave pitch body
        await client.patch(
            f"{QWOTED_BASE}/api/internal/pitches/{pitch_id}/autosave",
            json={"pitch": {"body": pitch_text}},
            headers=headers,
        )

        # 4. Submit with Rails array-append pitched_entities brackets (do NOT send pitch[draft]=false)
        submit_resp = await client.put(
            f"{QWOTED_BASE}/api/internal/pitches/{pitch_id}",
            data={
                "pitch[body]": pitch_text,
                "pitch[pitched_entities][][entity_id]": entity_id,
                "pitch[pitched_entities][][entity_type]": entity_type,
            },
            headers=headers,
        )
        if submit_resp.status_code not in (200, 201):
            raise QwotedAuthError(f"Qwoted pitch submit failed (HTTP {submit_resp.status_code})")

        return ("qwoted_live", f"Submitted pitch #{pitch_id} to Qwoted request {source_request_id}.")


def score_opportunity_for_client(client_obj: Client, opp: DiscoveredOpportunity) -> tuple[bool, float]:
    clf = HeuristicDecisionClassifier()
    score = clf.score_article_relevance(
        article_title=opp.title,
        article_summary=opp.summary or opp.publication,
        client_name=client_obj.name,
        client_industry=f"{client_obj.industry} {client_obj.brand_voice[:160]}".strip(),
    )
    return score.is_relevant, round(score.confidence, 4)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_opportunity_payload(raw: dict[str, Any]) -> DiscoveredOpportunity:
    return DiscoveredOpportunity(
        source_platform=str(raw.get("source_platform", "qwoted")),
        external_id=str(raw["external_id"]),
        title=str(raw["title"]),
        summary=str(raw.get("summary", "")),
        publication=str(raw.get("publication", "")),
        journalist_name=str(raw.get("journalist_name", "")),
        deadline=str(raw.get("deadline", "")),
        share_url=str(raw["share_url"]),
        hashtags=list(raw.get("hashtags", [])),
    )
