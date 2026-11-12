from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.models import Client, User
from app.services.discovery import discover_journalists_for_client
from app.services.tavily_search import (
    extract_tavily_urls,
    search_tavily_articles,
    search_tavily_bio,
)


@pytest.mark.asyncio
async def test_tavily_articles_bio_and_extract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key")
    get_settings.cache_clear()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        assert body["api_key"] == "tvly-test-key"
        if request.url.path == "/extract":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "url": "https://venturebeat.com/ai/infra-story",
                            "raw_content": "Full article text by Marcus Chen.",
                        }
                    ]
                },
            )
        if body.get("topic") == "news":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "Distributed AI Inference Benchmarks",
                            "url": "https://venturebeat.com/ai/infra-story",
                            "content": "Summary of benchmarks.",
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://x.com/marcuschen",
                        "content": "AI reporter at VentureBeat. Pitch: marcus.chen@venturebeat.com",
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    try:
        articles = await search_tavily_articles("Distributed AI", transport=transport)
        assert len(articles) == 1
        assert articles[0].outlet == "venturebeat.com"

        bio = await search_tavily_bio("Marcus Chen", "venturebeat.com", transport=transport)
        assert bio.email == "marcus.chen@venturebeat.com"
        assert bio.socials == [("x", "https://x.com/marcuschen")]

        extracted = await extract_tavily_urls(
            ["https://venturebeat.com/ai/infra-story"], transport=transport
        )
        assert "Marcus Chen" in extracted["https://venturebeat.com/ai/infra-story"]
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_discover_journalists_uses_tavily_when_configured(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EXA_API_KEY", "")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key")
    get_settings.cache_clear()

    officer = User(
        email="officer.tavily@example.com",
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

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.tavily.com":
            body = json.loads(request.content.decode("utf-8"))
            if body.get("topic") == "news":
                return httpx.Response(
                    200,
                    json={
                        "results": [
                            {
                                "title": "Distributed AI Inference Benchmarks",
                                "url": "https://venturebeat.com/ai/infra-story",
                                "content": "AI infrastructure story.",
                            }
                        ]
                    },
                )
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "url": "https://x.com/marcuschen",
                            "content": "Reach me: marcus.chen@venturebeat.com",
                        }
                    ]
                },
            )
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if request.url.path == "/ai/infra-story":
            return httpx.Response(
                200,
                text="<html><head><meta name='author' content='Marcus Chen'></head><body><p>By Marcus Chen</p></body></html>",
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
        assert discovered[0].canonical_name == "Marcus Chen"
        assert discovered[0].email == "marcus.chen@venturebeat.com"
    finally:
        get_settings.cache_clear()

