from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.core.config import get_settings
from app.services.email_verifier import (
    discover_verified_pattern_email,
    find_email_hunter,
    find_email_prospeo,
    infer_outlet_email_candidates,
    resolve_journalist_email,
)
from app.services.scraper import smart_fetch_html


def test_infer_outlet_email_candidates() -> None:
    assert infer_outlet_email_candidates("Jane Doe", "techcrunch.com") == [
        "jane.doe@techcrunch.com",
        "jane@techcrunch.com",
        "jdoe@techcrunch.com",
    ]
    assert infer_outlet_email_candidates("Jane Doe", "www.forbes.com") == [
        "jane.doe@forbes.com",
        "jane@forbes.com",
        "jdoe@forbes.com",
    ]
    # Single-token or empty names should not produce patterns
    assert infer_outlet_email_candidates("Atharva", "example.com") == []
    assert infer_outlet_email_candidates("Jane Doe", "localhost") == []


@pytest.mark.asyncio
async def test_discover_verified_pattern_email_none_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_VERIFIER_PROVIDER", "none")
    get_settings.cache_clear()
    try:
        email, status = await discover_verified_pattern_email("Jane Doe", "techcrunch.com")
        assert email is None
        assert status == "missing"
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_discover_verified_pattern_email_reoon_verified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EMAIL_VERIFIER_PROVIDER", "reoon")
    monkeypatch.setenv("EMAIL_VERIFIER_API_KEY", "test-reoon-key")
    get_settings.cache_clear()

    def handler(request: httpx.Request) -> httpx.Response:
        email_param = request.url.params.get("email", "")
        if email_param == "jane@techcrunch.com":
            return httpx.Response(200, json={"status": "safe"})
        return httpx.Response(200, json={"status": "invalid"})

    transport = httpx.MockTransport(handler)
    try:
        with patch("app.services.email_verifier.check_mx_record", return_value=True):
            email, status = await discover_verified_pattern_email(
                "Jane Doe", "techcrunch.com", transport=transport
            )
        assert email == "jane@techcrunch.com"
        assert status == "verified"
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_smart_fetch_html_uses_httpx_on_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><body>ok</body></html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        status, final_url, html = await smart_fetch_html(
            client,
            "https://example.com/article",
            user_agent="TestBot/1.0",
            allow_stealth_fallback=True,
        )
    assert status == 200
    assert "ok" in html
    assert final_url == "https://example.com/article"


@pytest.mark.asyncio
async def test_smart_fetch_html_stealth_fallback_on_403() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="Forbidden")

    fake_stealth_resp = type(
        "FakeResp",
        (),
        {
            "status_code": 200,
            "text": "<html><body>bypassed</body></html>",
            "url": "https://example.com/article",
        },
    )()

    class FakeSession:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> FakeSession:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        get = AsyncMock(return_value=fake_stealth_resp)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with patch("curl_cffi.requests.AsyncSession", FakeSession):
            status, final_url, html = await smart_fetch_html(
                client,
                "https://example.com/article",
                user_agent="TestBot/1.0",
                allow_stealth_fallback=True,
            )
    assert status == 200
    assert "bypassed" in html
    assert final_url == "https://example.com/article"


@pytest.mark.asyncio
async def test_hunter_and_prospeo_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUNTER_API_KEY", "hunter-key")
    monkeypatch.setenv("PROSPEO_API_KEY", "prospeo-key")
    get_settings.cache_clear()

    def hunter_handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.hunter.io":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "email": "jane.doe@techcrunch.com",
                        "verification": {"status": "valid"},
                    }
                },
            )
        return httpx.Response(404)

    def prospeo_handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.prospeo.io":
            assert request.headers.get("X-KEY") == "prospeo-key"
            return httpx.Response(
                200,
                json={
                    "error": False,
                    "response": {
                        "email": "jane@techcrunch.com",
                        "email_status": "VALID",
                    },
                },
            )
        return httpx.Response(404)

    try:
        email_h, status_h = await find_email_hunter(
            "Jane Doe", "techcrunch.com", transport=httpx.MockTransport(hunter_handler)
        )
        assert (email_h, status_h) == ("jane.doe@techcrunch.com", "verified")

        email_p, status_p = await find_email_prospeo(
            "Jane Doe", "techcrunch.com", transport=httpx.MockTransport(prospeo_handler)
        )
        assert (email_p, status_p) == ("jane@techcrunch.com", "verified")

        # Fallback from Hunter (404) -> Prospeo (VALID)
        email_r, status_r, src_r = await resolve_journalist_email(
            "Jane Doe", "techcrunch.com", transport=httpx.MockTransport(prospeo_handler)
        )
        assert (email_r, status_r, src_r) == ("jane@techcrunch.com", "verified", "prospeo")
    finally:
        get_settings.cache_clear()
