import asyncio
import hashlib
import ipaddress
import re
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import feedparser  # type: ignore[import-untyped]
import httpx
from protego import Protego  # type: ignore[import-untyped]
from scrapling.parser import Selector  # type: ignore[import-untyped]

from app.core.config import get_settings

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

_IGNORED_EMAIL_DOMAINS = (
    "mastodon.social",
    "bsky.social",
    "threads.net",
    "sentry.io",
    "wixpress.com",
    "schema.org",
    "w3.org",
)
_IGNORED_EMAIL_PREFIXES = (
    "noreply@",
    "no-reply@",
    "donotreply@",
    "support@",
    "privacy@",
    "legal@",
    "dmca@",
    "jobs@",
    "careers@",
    "unsubscribe@",
    "webmaster@",
)
_IGNORED_EMAIL_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".js", ".css")


def _decode_cfemail(hex_str: str) -> str | None:
    """Decode Cloudflare's obfuscated data-cfemail hex string."""
    try:
        raw = bytes.fromhex(hex_str.strip())
        if len(raw) < 2:
            return None
        key = raw[0]
        return "".join(chr(b ^ key) for b in raw[1:])
    except Exception:
        return None


def _is_usable_email(candidate: str) -> bool:
    lower = candidate.strip().lower()
    if not _EMAIL_RE.fullmatch(lower):
        return False
    if lower.endswith(_IGNORED_EMAIL_EXTS):
        return False
    if any(lower.startswith(p) for p in _IGNORED_EMAIL_PREFIXES):
        return False
    domain = lower.split("@", 1)[1]
    if domain in _IGNORED_EMAIL_DOMAINS or any(domain.endswith(f".{d}") for d in _IGNORED_EMAIL_DOMAINS):
        return False
    return True


def _extract_best_email(sel: Selector, author_name: str = "") -> str | None:
    """Extract the best publicly listed journalist email from mailto, JSON-LD, cfemail, or body."""
    candidates: list[str] = []

    for href in sel.css("a[href^='mailto:']::attr(href)").getall():
        cand = str(href).replace("mailto:", "").split("?")[0].strip().lower()
        if _is_usable_email(cand) and cand not in candidates:
            candidates.append(cand)

    for cf_hex in sel.css("[data-cfemail]::attr(data-cfemail)").getall():
        decoded = _decode_cfemail(str(cf_hex))
        if decoded and _is_usable_email(decoded.lower()) and decoded.lower() not in candidates:
            candidates.append(decoded.lower())

    # JSON-LD often carries full author bios & contact emails (e.g. TechCrunch, WordPress)
    for ld_text in sel.css("script[type='application/ld+json']::text").getall():
        for match in _EMAIL_RE.findall(str(ld_text)):
            cand = match.lower()
            if _is_usable_email(cand) and cand not in candidates:
                candidates.append(cand)

    visible_text = " ".join(str(t) for t in sel.css("body ::text").getall())
    for match in _EMAIL_RE.findall(visible_text):
        cand = match.lower()
        if _is_usable_email(cand) and cand not in candidates:
            candidates.append(cand)

    if not candidates:
        return None

    name_tokens = [tok.lower() for tok in re.findall(r"[A-Za-z]{3,}", author_name)]
    for cand in candidates:
        local = cand.split("@", 1)[0]
        if any(tok in local for tok in name_tokens):
            return cand
    return candidates[0]


class ScrapeBlockedError(Exception):
    """Raised when SSRF guard or robots.txt forbids fetching a URL."""


@dataclass(frozen=True)
class ScrapedArticle:
    title: str
    url: str
    published_at: datetime | None
    summary: str
    source_url: str
    scraped_at: datetime
    content_hash: str


@dataclass(frozen=True)
class ScrapedSocial:
    platform: str  # linkedin | x | bluesky | website | other
    handle_or_url: str
    source_url: str
    scraped_at: datetime


@dataclass
class ScrapedJournalist:
    canonical_name: str
    outlet: str
    beat: str
    email: str | None
    email_source_url: str | None
    email_scraped_at: datetime | None
    profile_source_url: str
    profile_scraped_at: datetime
    confidence_score: float
    socials: list[ScrapedSocial] = field(default_factory=list)
    articles: list[ScrapedArticle] = field(default_factory=list)


def validate_public_http_url(url: str) -> str:
    """Ensure URL is public http(s) and does not resolve to private/loopback IPs (SSRF guard)."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc or not parsed.hostname:
        raise ScrapeBlockedError("Only public http(s) URLs are allowed")

    hostname = parsed.hostname.lower()
    if hostname in ("localhost", "localhost.localdomain") or hostname.endswith(".local"):
        raise ScrapeBlockedError(f"Blocked local hostname: {hostname}")

    try:
        ip_obj = ipaddress.ip_address(hostname)
        if not ip_obj.is_global:
            raise ScrapeBlockedError(f"Blocked non-global IP: {hostname}")
    except ValueError:
        try:
            infos = socket.getaddrinfo(hostname, None)
            for info in infos:
                addr = info[4][0]
                ip_obj = ipaddress.ip_address(addr)
                if not ip_obj.is_global:
                    raise ScrapeBlockedError(f"Hostname {hostname} resolves to non-global IP {addr}")
        except socket.gaierror:
            # Allow unresolved hostnames to fail naturally at HTTP layer or in mocked tests
            pass

    return parsed.geturl()


class DomainRateLimiter:
    """Per-domain async rate limiter respecting SCRAPE_DOMAIN_DELAY_SEC."""

    def __init__(self, delay_seconds: float) -> None:
        self.delay_seconds = delay_seconds
        self._last_request: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def acquire(self, domain: str) -> None:
        lock = self._locks.setdefault(domain, asyncio.Lock())
        async with lock:
            now = time.monotonic()
            last = self._last_request.get(domain, 0.0)
            wait_for = self.delay_seconds - (now - last)
            if wait_for > 0:
                await asyncio.sleep(wait_for)
            self._last_request[domain] = time.monotonic()


class RobotsChecker:
    """Fetches, parses, and caches /robots.txt per domain using Protego."""

    def __init__(self, user_agent: str) -> None:
        self.user_agent = user_agent
        self._cache: dict[str, Protego] = {}

    async def can_fetch(self, client: httpx.AsyncClient, url: str) -> bool:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        parser = self._cache.get(domain)
        if parser is None:
            robots_url = f"{parsed.scheme}://{domain}/robots.txt"
            content = ""
            try:
                resp = await client.get(robots_url, headers={"User-Agent": self.user_agent})
                if resp.status_code == 200:
                    content = resp.text.lstrip("\ufeff")
                elif 400 <= resp.status_code < 500:
                    content = ""
                else:
                    content = "User-agent: *\nDisallow: /"
            except Exception:
                content = "User-agent: *\nDisallow: /"
            parser = Protego.parse(content)
            self._cache[domain] = parser

        return bool(parser.can_fetch(url, self.user_agent))


async def smart_fetch_html(
    client: httpx.AsyncClient,
    url: str,
    *,
    user_agent: str,
    allow_stealth_fallback: bool = True,
) -> tuple[int, str, str]:
    """Fetch HTML via httpx, falling back to curl_cffi Chrome TLS impersonation on 403/429."""
    resp = await client.get(url, headers={"User-Agent": user_agent})
    if resp.status_code == 200 or not allow_stealth_fallback:
        return resp.status_code, str(resp.url), resp.text

    if resp.status_code in (403, 429):
        try:
            from curl_cffi.requests import AsyncSession  # type: ignore[import-untyped]

            async with AsyncSession(impersonate="chrome", timeout=12) as session:
                stealth_resp = await session.get(
                    url,
                    headers={"User-Agent": user_agent},
                    allow_redirects=True,
                )
            status_code = int(getattr(stealth_resp, "status_code", 0) or 0)
            body = getattr(stealth_resp, "text", "") or ""
            final_url = str(getattr(stealth_resp, "url", url) or url)
            if status_code == 200 and body:
                return 200, final_url, body
        except Exception:
            pass
    return resp.status_code, str(resp.url), resp.text


def _classify_social_url(href: str) -> str | None:
    lower = href.lower()
    if "linkedin.com/in/" in lower:
        return "linkedin"
    if "x.com/" in lower or "twitter.com/" in lower:
        return "x"
    if "bsky.app/profile/" in lower:
        return "bluesky"
    return None


def extract_journalist_from_html(
    html: str,
    *,
    source_url: str,
    outlet: str = "",
    beat: str = "",
    max_articles: int = 5,
) -> ScrapedJournalist:
    """Parse publicly listed journalist info & recent articles from an author/profile HTML page."""
    now = datetime.now(timezone.utc)
    sel = Selector(html)

    name = (
        sel.css("h1::text").get()
        or sel.css("meta[name='author']::attr(content)").get()
        or sel.css("title::text").get()
        or "Unknown Journalist"
    ).strip()

    inferred_outlet = (
        outlet
        or sel.css("meta[property='og:site_name']::attr(content)").get()
        or urlparse(source_url).netloc
    ).strip()

    # Extract only publicly listed email (mailto:, cfemail, JSON-LD, visible text)
    email = _extract_best_email(sel, author_name=name)

    socials: list[ScrapedSocial] = []
    seen_socials: set[str] = set()
    for href in sel.css("a::attr(href)").getall():
        full_href = urljoin(source_url, str(href).strip())
        platform = _classify_social_url(full_href)
        if platform and full_href not in seen_socials:
            seen_socials.add(full_href)
            socials.append(
                ScrapedSocial(
                    platform=platform,
                    handle_or_url=full_href,
                    source_url=source_url,
                    scraped_at=now,
                )
            )

    articles: list[ScrapedArticle] = []
    seen_urls: set[str] = set()
    for node in sel.css("article, .article, .post, .story"):
        if len(articles) >= max_articles:
            break
        title = (
            node.css("h1::text, h2::text, h3::text, a::text").get() or ""
        ).strip()
        art_href = node.css("a::attr(href)").get()
        if not title or not art_href:
            continue
        art_url = urljoin(source_url, str(art_href).strip())
        if art_url in seen_urls:
            continue
        seen_urls.add(art_url)
        summary = " ".join(t.strip() for t in node.css("p::text").getall() if t.strip())[:500]
        pub_raw = node.css("time::attr(datetime)").get()
        pub_dt: datetime | None = None
        if pub_raw:
            try:
                pub_dt = datetime.fromisoformat(pub_raw.replace("Z", "+00:00"))
            except ValueError:
                pub_dt = None
        articles.append(
            ScrapedArticle(
                title=title,
                url=art_url,
                published_at=pub_dt,
                summary=summary,
                source_url=source_url,
                scraped_at=now,
                content_hash=hashlib.sha256(f"{title}|{art_url}".encode()).hexdigest(),
            )
        )

    confidence = 0.4 + (0.2 if email else 0.0) + (0.2 if articles else 0.0) + (0.2 if socials else 0.0)
    return ScrapedJournalist(
        canonical_name=name,
        outlet=inferred_outlet,
        beat=beat,
        email=email,
        email_source_url=source_url if email else None,
        email_scraped_at=now if email else None,
        profile_source_url=source_url,
        profile_scraped_at=now,
        confidence_score=round(min(confidence, 1.0), 2),
        socials=socials,
        articles=articles,
    )


def extract_articles_from_rss(
    xml_text: str, *, source_url: str, max_articles: int = 5
) -> list[ScrapedArticle]:
    """Parse recent articles from an RSS/Atom feed."""
    now = datetime.now(timezone.utc)
    parsed = feedparser.parse(xml_text)
    results: list[ScrapedArticle] = []
    for entry in parsed.entries[:max_articles]:
        title = str(getattr(entry, "title", "")).strip()
        link = str(getattr(entry, "link", "")).strip()
        if not title or not link:
            continue
        summary = str(getattr(entry, "summary", "")).strip()[:500]
        results.append(
            ScrapedArticle(
                title=title,
                url=link,
                published_at=None,
                summary=summary,
                source_url=source_url,
                scraped_at=now,
                content_hash=hashlib.sha256(f"{title}|{link}".encode()).hexdigest(),
            )
        )
    return results


async def scrape_journalist_page(
    url: str,
    *,
    outlet: str = "",
    beat: str = "",
    rss_url: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ScrapedJournalist:
    """Fetch a public journalist profile (and optional RSS feed) respecting robots.txt and rate limits."""
    settings = get_settings()
    clean_url = validate_public_http_url(url)
    clean_rss = validate_public_http_url(rss_url) if rss_url else None

    rate_limiter = DomainRateLimiter(delay_seconds=float(settings.SCRAPE_DOMAIN_DELAY_SEC))
    robots = RobotsChecker(user_agent=settings.SCRAPER_USER_AGENT)

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(15.0),
        follow_redirects=True,
        transport=transport,
    ) as client:
        domain = urlparse(clean_url).netloc.lower()
        await rate_limiter.acquire(domain)
        if not await robots.can_fetch(client, clean_url):
            raise ScrapeBlockedError(f"Blocked by robots.txt: {clean_url}")

        resp = await client.get(clean_url, headers={"User-Agent": settings.SCRAPER_USER_AGENT})
        resp.raise_for_status()
        journalist = extract_journalist_from_html(
            resp.text, source_url=clean_url, outlet=outlet, beat=beat
        )

        if clean_rss:
            rss_domain = urlparse(clean_rss).netloc.lower()
            await rate_limiter.acquire(rss_domain)
            if await robots.can_fetch(client, clean_rss):
                rss_resp = await client.get(
                    clean_rss, headers={"User-Agent": settings.SCRAPER_USER_AGENT}
                )
                if rss_resp.status_code == 200:
                    rss_articles = extract_articles_from_rss(rss_resp.text, source_url=clean_rss)
                    existing_urls = {a.url for a in journalist.articles}
                    for art in rss_articles:
                        if art.url not in existing_urls and len(journalist.articles) < 5:
                            journalist.articles.append(art)

    return journalist
