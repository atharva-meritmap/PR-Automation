import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.services.email_verifier import verify_email_address
from app.services.journalists import upsert_scraped_journalist
from app.services.scraper import (
    ScrapeBlockedError,
    extract_journalist_from_html,
    scrape_journalist_page,
    validate_public_http_url,
)

SAMPLE_AUTHOR_HTML = """
<!doctype html>
<html>
  <head>
    <title>Jane Doe - TechDaily</title>
    <meta property="og:site_name" content="TechDaily" />
  </head>
  <body>
    <h1>Jane Doe</h1>
    <p>Senior AI Reporter. Contact: <a href="mailto:jane.doe@example.com">jane.doe@example.com</a></p>
    <a href="https://www.linkedin.com/in/janedoe-tech">LinkedIn</a>
    <a href="https://x.com/janedoe">X</a>
    <article>
      <h2><a href="/2026/ai-chips">AI Chips Surge in 2026</a></h2>
      <time datetime="2026-10-15T10:00:00Z">Oct 15, 2026</time>
      <p>Semiconductor startups raised record funding this quarter.</p>
    </article>
    <article>
      <h2><a href="/2026/robotics-boom">Robotics Boom in Manufacturing</a></h2>
      <time datetime="2026-10-01T10:00:00Z">Oct 1, 2026</time>
      <p>Industrial automation is accelerating across factories.</p>
    </article>
  </body>
</html>
"""


def test_ssrf_guard_blocks_private_urls() -> None:
    for bad in (
        "http://localhost:8000/admin",
        "http://127.0.0.1/secret",
        "http://192.168.1.1/",
        "ftp://example.com/file",
    ):
        with pytest.raises(ScrapeBlockedError):
            validate_public_http_url(bad)

    assert validate_public_http_url("https://example.com/author/jane") == "https://example.com/author/jane"


def test_extract_journalist_from_html_with_provenance() -> None:
    scraped = extract_journalist_from_html(
        SAMPLE_AUTHOR_HTML,
        source_url="https://example.com/author/jane",
        beat="AI & Robotics",
    )
    assert scraped.canonical_name == "Jane Doe"
    assert scraped.outlet == "TechDaily"
    assert scraped.email == "jane.doe@example.com"
    assert scraped.email_source_url == "https://example.com/author/jane"
    assert scraped.email_scraped_at is not None
    assert len(scraped.socials) == 2
    assert {s.platform for s in scraped.socials} == {"linkedin", "x"}
    assert len(scraped.articles) == 2
    assert scraped.articles[0].url == "https://example.com/2026/ai-chips"
    assert scraped.articles[0].source_url == "https://example.com/author/jane"


@pytest.mark.asyncio
async def test_robots_txt_disallow_blocks_scrape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private/\n")
        return httpx.Response(200, text=SAMPLE_AUTHOR_HTML)

    transport = httpx.MockTransport(handler)
    with pytest.raises(ScrapeBlockedError):
        await scrape_journalist_page(
            "https://93.184.216.34/private/author",
            transport=transport,
        )


@pytest.mark.asyncio
async def test_scrape_and_dedupe_across_upserts(db_session: AsyncSession) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return httpx.Response(200, text=SAMPLE_AUTHOR_HTML)

    transport = httpx.MockTransport(handler)
    scraped = await scrape_journalist_page(
        "https://93.184.216.34/author/jane",
        beat="AI",
        transport=transport,
    )
    j1 = await upsert_scraped_journalist(db_session, scraped)
    j2 = await upsert_scraped_journalist(db_session, scraped)
    assert j1.id == j2.id
    assert j1.email_status == "unverified"
    assert len(j1.articles) == 2


@pytest.mark.asyncio
async def test_email_verification_honest_statuses(monkeypatch: pytest.MonkeyPatch) -> None:
    # Missing & invalid syntax
    assert (await verify_email_address(None)).status == "missing"
    assert (await verify_email_address("not-an-email")).status == "invalid"

    # No MX -> invalid
    monkeypatch.setattr("app.services.email_verifier.check_mx_record", lambda _d: None)
    res_no_mx = await verify_email_address("jane@example.com")
    assert res_no_mx.status == "invalid"

    # Valid MX + provider=none -> MUST remain unverified (never verified)
    monkeypatch.setattr("app.services.email_verifier.check_mx_record", lambda _d: "mx.example.com")
    settings = get_settings()
    monkeypatch.setattr(settings, "EMAIL_VERIFIER_PROVIDER", "none")
    res_unverified = await verify_email_address("jane@example.com")
    assert res_unverified.status == "unverified"

    # Mocked Reoon safe -> verified; catch_all -> risky
    monkeypatch.setattr(settings, "EMAIL_VERIFIER_PROVIDER", "reoon")
    monkeypatch.setattr(settings, "EMAIL_VERIFIER_API_KEY", "test-key")
    safe_transport = httpx.MockTransport(lambda _r: httpx.Response(200, json={"status": "safe"}))
    assert (await verify_email_address("jane@example.com", transport=safe_transport)).status == "verified"

    catchall_transport = httpx.MockTransport(lambda _r: httpx.Response(200, json={"status": "catch_all"}))
    assert (await verify_email_address("jane@example.com", transport=catchall_transport)).status == "risky"
