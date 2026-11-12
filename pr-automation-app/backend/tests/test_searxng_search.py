from __future__ import annotations

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.models import Client, User
from app.services.discovery import discover_journalists_for_client
from app.services.searxng_search import dork_journalist_bio_searxng


@pytest.mark.asyncio
async def test_searxng_bio_dorking_extracts_email_and_socials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SEARXNG_BASE_URL", "http://searxng.local:8080")
    get_settings.cache_clear()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/search"
        assert request.url.params.get("format") == "json"
        q = request.url.params.get("q", "")
        assert '"Maya Lin"' in q
        assert "site:muckrack.com" in q
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Maya Lin on X",
                        "url": "https://x.com/mayalin",
                        "content": "AI & cloud reporter. Tips: maya.lin@techcrunch.com",
                    },
                    {
                        "title": "Maya Lin | Muck Rack",
                        "url": "https://muckrack.com/maya-lin",
                        "content": "Journalist at TechCrunch",
                    },
                    {
                        "title": "Maya Lin - LinkedIn",
                        "url": "https://www.linkedin.com/in/maya-lin",
                        "content": "",
                    },
                ]
            },
        )

    try:
        bio = await dork_journalist_bio_searxng(
            "Maya Lin", "TechCrunch", transport=httpx.MockTransport(handler)
        )
        assert bio.email == "maya.lin@techcrunch.com"
        assert bio.email_source_url == "https://x.com/mayalin"
        assert ("x", "https://x.com/mayalin") in bio.socials
        assert ("muckrack", "https://muckrack.com/maya-lin") in bio.socials
        assert ("linkedin", "https://www.linkedin.com/in/maya-lin") in bio.socials
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_discover_journalists_uses_searxng_dorking(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EXA_API_KEY", "")
    monkeypatch.setenv("TAVILY_API_KEY", "")
    monkeypatch.setenv("SEARXNG_BASE_URL", "http://searxng.local:8080")
    get_settings.cache_clear()

    officer = User(
        email="officer.searxng@example.com",
        password_hash=hash_password("Pass1234!"),
        full_name="PR Officer",
        role="officer",
        is_active=True,
    )
    db_session.add(officer)
    await db_session.flush()

    client_obj = Client(
        officer_id=officer.id,
        name="Atharva",
        domain="",
        industry="Distributed AI Systems",
        brand_voice="Built 1M RPS inference engine",
        key_facts={"visa_category": "EB-1A"},
    )
    db_session.add(client_obj)
    await db_session.commit()
    await db_session.refresh(client_obj)

    rss_xml = """<?xml version="1.0" encoding="UTF-8" ?>
    <rss version="2.0"><channel><title>Bing News</title>
      <item>
        <title>AI Inference Breakthrough</title>
        <link>https://techcrunch.com/2026/10/01/ai-inference/</link>
        <author>Maya Lin</author>
      </item>
    </channel></rss>"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "searxng.local":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "url": "https://x.com/mayalin",
                            "content": "Contact me at maya.lin@techcrunch.com",
                        }
                    ]
                },
            )
        if "bing.com" in request.url.host:
            return httpx.Response(200, text=rss_xml)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if request.url.path == "/2026/10/01/ai-inference/":
            return httpx.Response(
                200,
                text="<html><body><h1>AI Inference Breakthrough</h1><p>By Maya Lin</p></body></html>",
            )
        return httpx.Response(404)

    try:
        discovered = await discover_journalists_for_client(
            db_session,
            client_obj,
            max_results=1,
            transport=httpx.MockTransport(handler),
        )
        assert len(discovered) == 1
        assert discovered[0].canonical_name == "Maya Lin"
        assert discovered[0].email == "maya.lin@techcrunch.com"
    finally:
        get_settings.cache_clear()
