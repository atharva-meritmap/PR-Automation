import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.db.models import Client, PrOpportunity, User
from app.services.qwoted import (
    discover_public_opportunities,
    draft_candidate_pitch,
    login_qwoted,
    score_opportunity_for_client,
    search_qwoted_opportunities,
    submit_qwoted_pitch,
)

SIGNIN_HTML = """
<form action="/users/sign_in" method="post">
  <input type="hidden" name="authenticity_token" value="csrf_tok_123" />
</form>
"""

SOURCE_REQ_HTML = """
<div data-react-class="source_requests/top_level_search"
     data-react-props="{&quot;algoliaAppId&quot;:&quot;APP123&quot;,&quot;algoliaSearchKey&quot;:&quot;KEY123&quot;,&quot;algoliaIndexName&quot;:&quot;SourceRequest_production&quot;}">
</div>
"""

PUBLIC_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Bing News</title>
  <item>
    <title>TechCrunch seeking experts on distributed AI infrastructure</title>
    <link>https://www.bing.com/news/apiclick.aspx?url=https%3a%2f%2fexample.org%2fcallout-ai</link>
    <description>Looking for Principal Engineers to comment on AI storage systems.</description>
    <source>ExampleTech</source>
  </item>
</channel></rss>
"""


@pytest.mark.asyncio
async def test_qwoted_login_search_and_dry_run_pitch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/users/sign_in" and request.method == "GET":
            return httpx.Response(200, text=SIGNIN_HTML)
        if request.url.path == "/users/sign_in" and request.method == "POST":
            return httpx.Response(
                302,
                headers={
                    "Location": "https://app.qwoted.com/source_requests",
                    "Set-Cookie": "_qwoted_session=abc123; Path=/",
                },
            )
        if request.url.path == "/source_requests":
            return httpx.Response(200, text=SOURCE_REQ_HTML)
        if "algolia.net" in request.url.host:
            return httpx.Response(
                200,
                json={
                    "hits": [
                        {
                            "objectID": "99001",
                            "name": "Seeking Distributed Systems Leaders for Cloud Storage Piece",
                            "details": "Looking for engineering leaders who scaled storage virtualization.",
                            "publication_name": "InfoQ",
                            "reporter_name": "Dana Kim",
                            "deadline": "2026-11-20",
                            "hashtags": ["#cloud", "#storage"],
                        }
                    ]
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    cookies = await login_qwoted("officer@example.com", "secret", transport=transport)
    assert cookies["_qwoted_session"] == "abc123"

    hits = await search_qwoted_opportunities(cookies, "distributed systems", transport=transport)
    assert len(hits) == 1
    assert hits[0].external_id == "99001"
    assert hits[0].publication == "InfoQ"
    assert hits[0].journalist_name == "Dana Kim"

    mode, detail = await submit_qwoted_pitch(
        cookies,
        source_request_id="99001",
        pitch_text="Verified pitch text",
        confirm_live_submit=False,
        transport=transport,
    )
    assert mode == "dry_run"
    assert "99001" in detail


@pytest.mark.asyncio
async def test_public_opportunity_discovery_and_grounded_pitch(db_session: AsyncSession) -> None:
    officer = User(
        email="opp.officer@example.com",
        password_hash=hash_password("Pass1234!"),
        full_name="PR Officer",
        role="officer",
    )
    db_session.add(officer)
    await db_session.flush()

    client = Client(
        officer_id=officer.id,
        name="Atharva",
        domain="",
        industry="Software Engineering",
        brand_voice="Principal Software Engineering Manager leading Azure Host Storage.",
        key_facts={"visa_category": "EB-1A", "role_title": "Principal Manager", "company": "Microsoft"},
    )
    db_session.add(client)
    await db_session.commit()
    await db_session.refresh(client)

    def handler(request: httpx.Request) -> httpx.Response:
        if "bing.com" in request.url.host:
            return httpx.Response(200, text=PUBLIC_RSS)
        return httpx.Response(404)

    opps = await discover_public_opportunities(
        client, max_results=5, transport=httpx.MockTransport(handler)
    )
    assert len(opps) == 1
    assert opps[0].share_url == "https://example.org/callout-ai"
    is_rel, conf = score_opportunity_for_client(client, opps[0])
    assert conf > 0

    opp_row = PrOpportunity(
        client_id=client.id,
        source_platform=opps[0].source_platform,
        external_id=opps[0].external_id,
        title=opps[0].title,
        summary=opps[0].summary,
        publication=opps[0].publication,
        journalist_name=opps[0].journalist_name,
        deadline=opps[0].deadline,
        share_url=opps[0].share_url,
        hashtags=opps[0].hashtags,
        is_relevant=is_rel,
        confidence=conf,
    )
    pitch = draft_candidate_pitch(client, opp_row)
    assert "Atharva" in pitch
    assert "Azure Host Storage" in pitch
