import re
from dataclasses import dataclass

import dns.resolver
import httpx

from app.core.config import get_settings

_SYNTAX_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


@dataclass(frozen=True)
class EmailVerificationResult:
    status: str  # verified | risky | unverified | invalid | missing
    mx_host: str | None
    detail: str


def check_mx_record(domain: str) -> str | None:
    """Return primary MX hostname for domain, or None if no MX exists."""
    try:
        answers = dns.resolver.resolve(domain, "MX")
        records = sorted(answers, key=lambda r: int(r.preference))
        if records:
            return str(records[0].exchange).rstrip(".").lower()
    except Exception:
        return None
    return None


async def verify_email_address(
    email: str | None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> EmailVerificationResult:
    """Verify an email honestly: never mark guessed/unverified addresses as verified."""
    if not email:
        return EmailVerificationResult(status="missing", mx_host=None, detail="No email provided")

    clean = email.strip().lower()
    if not _SYNTAX_RE.fullmatch(clean):
        return EmailVerificationResult(status="invalid", mx_host=None, detail="Invalid email syntax")

    domain = clean.split("@", 1)[1]
    mx_host = check_mx_record(domain)
    if mx_host is None:
        return EmailVerificationResult(status="invalid", mx_host=None, detail="No MX record for domain")

    settings = get_settings()
    provider = settings.EMAIL_VERIFIER_PROVIDER
    api_key = settings.EMAIL_VERIFIER_API_KEY

    if provider == "none" or not api_key:
        # Without an external verifier, valid MX means 'unverified' (never 'verified')
        return EmailVerificationResult(
            status="unverified",
            mx_host=mx_host,
            detail="MX valid; external verification not configured",
        )

    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0), transport=transport) as client:
        if provider == "reoon":
            resp = await client.get(
                "https://emailverifier.reoon.com/api/v1/verify",
                params={"email": clean, "key": api_key, "mode": "power"},
            )
            resp.raise_for_status()
            verdict = str(resp.json().get("status", "")).lower()
            if verdict == "safe":
                return EmailVerificationResult("verified", mx_host, "Reoon: safe")
            if verdict in ("catch_all", "unknown", "role_account"):
                return EmailVerificationResult("risky", mx_host, f"Reoon: {verdict}")
            return EmailVerificationResult("invalid", mx_host, f"Reoon: {verdict}")

        if provider == "zerobounce":
            resp = await client.get(
                "https://api.zerobounce.net/v2/validate",
                params={"api_key": api_key, "email": clean},
            )
            resp.raise_for_status()
            verdict = str(resp.json().get("status", "")).lower()
            if verdict == "valid":
                return EmailVerificationResult("verified", mx_host, "ZeroBounce: valid")
            if verdict in ("catch-all", "unknown"):
                return EmailVerificationResult("risky", mx_host, f"ZeroBounce: {verdict}")
            return EmailVerificationResult("invalid", mx_host, f"ZeroBounce: {verdict}")

    return EmailVerificationResult(
        status="unverified",
        mx_host=mx_host,
        detail="Unsupported verifier provider; kept unverified",
    )

