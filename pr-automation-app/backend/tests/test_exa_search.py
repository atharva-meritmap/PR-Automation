from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.models import Client, User
from app.services.discovery import discover_journalists_for_client
from app.services.exa_search import (
    search_articles_with_authors,
    search_company_context,
    search_journalist_bio,
)


@pytest.mark.asyncio
async def test_exa_search_articles_payload_and_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EXA_API_KEY", "exa-test-key")
    get_settings.cache_clear()

    captured_payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("x-api-key") == "exa-test-key"
        body = json.loads(request.content.decode("utf-8"))
        captured_payloads.append(body)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Vector Indexing at 1M RPS",
                        "url": "https://techcrunch.com/2026/10/01/vector-indexing/",
                        "highlights": ["Low-latency distributed AI inference."],
                    }
                ],
                "output": {
                    "content": {
                        "articles": [
                            {
                                "author": "Elena Vance",
                                "url": "https://techcrunch.com/2026/10/01/vector-indexing/",
                                "title": "Vector Indexing at 1M RPS",
                                "outlet": "TechCrunch",
                            }
                        ]
                    }
                },
            },
        )

    try:
        hits = await search_articles_with_authors(
            "Distributed AI Systems",
            transport=httpx.MockTransport(handler),
        )
        assert len(hits) == 1
        assert hits[0].author == "Elena Vance"
        assert hits[0].outlet == "TechCrunch"

        sent = captured_payloads[0]
        assert sent["type"] == "auto"
        assert sent["contents"] == {"highlights": True}
        assert "systemPrompt" in sent
        assert "outputSchema" in sent
        assert "category" not in sent
        assert "includeDomains" not in sent
        assert "maxAgeHours" not in sent
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_exa_search_bio_and_company(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXA_API_KEY", "exa-test-key")
    get_settings.cache_clear()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        if body.get("category") == "company":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"title": "ScaleAI Newsroom", "url": "https://scale.com/news"}
                    ]
                },
            )
        assert "objective" in body
        assert body["contents"] == {"highlights": True}
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Elena Vance (@elenavance) / X",
                        "url": "https://x.com/elenavance",
                        "highlights": ["Tips & pitches: elena.vance@techcrunch.com"],
                    },
                    {
                        "title": "Elena Vance - LinkedIn",
                        "url": "https://www.linkedin.com/in/elena-vance",
                        "highlights": [],
                    },
                ]
            },
        )

    try:
        transport = httpx.MockTransport(handler)
        bio = await search_journalist_bio("Elena Vance", "TechCrunch", transport=transport)
        assert bio.email == "elena.vance@techcrunch.com"
        assert ("x", "https://x.com/elenavance") in bio.socials
        assert ("linkedin", "https://www.linkedin.com/in/elena-vance") in bio.socials

        company_docs = await search_company_context("ScaleAI", transport=transport)
        assert company_docs == [
            {"title": "ScaleAI Newsroom", "url": "https://scale.com/news"}
        ]
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_discover_journalists_uses_exa_when_configured(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EXA_API_KEY", "exa-test-key")
    get_settings.cache_clear()

    officer = User(
        email="officer.exa@example.com",
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
        if request.url.host == "api.exa.ai":
            body = json.loads(request.content.decode("utf-8"))
            if "outputSchema" in body:
                return httpx.Response(
                    200,
                    json={
                        "output": {
                            "content": {
                                "articles": [
                                    {
                                        "author": "Elena Vance",
                                        "url": "https://techcrunch.com/2026/10/01/vector-indexing/",
                                        "title": "Vector Indexing at 1M RPS",
                                        "outlet": "TechCrunch",
                                    }
                                ]
                            }
                        },
                        "results": [],
                    },
                )
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "url": "https://x.com/elenavance",
                            "highlights": ["Reach me at elena.vance@techcrunch.com"],
                        }
                    ]
                },
            )
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if request.url.path == "/2026/10/01/vector-indexing/":
            return httpx.Response(
                200,
                text="<html><body><h1>Vector Indexing</h1><p>By Elena Vance</p></body></html>",
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
        assert discovered[0].canonical_name == "Elena Vance"
        assert discovered[0].email == "elena.vance@techcrunch.com"
    finally:
        get_settings.cache_clear()

