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

    if (provider == "none" or not api_key) and settings.HUNTER_API_KEY:
        provider = "hunter"
        api_key = settings.HUNTER_API_KEY

    if provider == "none" or not api_key:
        # Without an external verifier, valid MX means 'unverified' (never 'verified')
        return EmailVerificationResult(
            status="unverified",
            mx_host=mx_host,
            detail="MX valid; external verification not configured",
        )

    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0), transport=transport) as client:
        if provider == "hunter":
            resp = await client.get(
                "https://api.hunter.io/v2/email-verifier",
                params={"email": clean, "api_key": api_key},
            )
            resp.raise_for_status()
            data = resp.json().get("data") or {}
            verdict = str(data.get("status") or "").lower()
            result = str(data.get("result") or "").lower()
            if verdict == "valid" or result == "deliverable":
                return EmailVerificationResult("verified", mx_host, f"Hunter: {verdict or result}")
            if verdict in ("accept_all", "unknown", "webmail") or result == "risky":
                return EmailVerificationResult("risky", mx_host, f"Hunter: {verdict or result}")
            return EmailVerificationResult("invalid", mx_host, f"Hunter: {verdict or result}")
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


_PUBLIC_AGGREGATOR_DOMAINS = (
    "bing.com",
    "google.com",
    "msn.com",
    "yahoo.com",
    "gmail.com",
    "outlook.com",
    "substack.com",
    "medium.com",
)


def _split_name_and_domain(full_name: str, outlet_domain: str) -> tuple[str, str, str] | None:
    domain = outlet_domain.strip().lower().removeprefix("www.")
    if not domain or "." not in domain or domain in _PUBLIC_AGGREGATOR_DOMAINS:
        return None
    parts = [re.sub(r"[^a-z]", "", p.lower()) for p in full_name.split()]
    parts = [p for p in parts if p]
    if len(parts) < 2:
        return None
    first, last = parts[0], parts[-1]
    if len(first) < 2 or len(last) < 2:
        return None
    return first, last, domain


def infer_outlet_email_candidates(full_name: str, outlet_domain: str) -> list[str]:
    """Generate cleanroom corporate email patterns for a journalist at a valid outlet domain."""
    parsed = _split_name_and_domain(full_name, outlet_domain)
    if parsed is None:
        return []
    first, last, domain = parsed
    return [
        f"{first}.{last}@{domain}",
        f"{first}@{domain}",
        f"{first[0]}{last}@{domain}",
    ]


async def find_email_hunter(
    full_name: str,
    outlet_domain: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[str | None, str]:
    """Resolve and verify a journalist's corporate email via Hunter.io Email Finder."""
    settings = get_settings()
    if not settings.HUNTER_API_KEY:
        return None, "missing"
    parsed = _split_name_and_domain(full_name, outlet_domain)
    if parsed is None:
        return None, "missing"
    first, last, domain = parsed

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12.0), transport=transport) as client:
            resp = await client.get(
                "https://api.hunter.io/v2/email-finder",
                params={
                    "domain": domain,
                    "first_name": first,
                    "last_name": last,
                    "api_key": settings.HUNTER_API_KEY,
                },
            )
            if resp.status_code != 200:
                return None, "missing"
            data = resp.json().get("data") or {}
            email = str(data.get("email") or "").strip().lower()
            verif_status = str((data.get("verification") or {}).get("status") or "").lower()
            if email and verif_status == "valid":
                return email, "verified"
    except Exception:
        return None, "missing"
    return None, "missing"


async def find_email_prospeo(
    full_name: str,
    outlet_domain: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[str | None, str]:
    """Resolve and verify a journalist's corporate email via Prospeo Email Finder."""
    settings = get_settings()
    if not settings.PROSPEO_API_KEY:
        return None, "missing"
    parsed = _split_name_and_domain(full_name, outlet_domain)
    if parsed is None:
        return None, "missing"
    first, last, domain = parsed

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12.0), transport=transport) as client:
            resp = await client.post(
                "https://api.prospeo.io/email-finder",
                headers={
                    "Content-Type": "application/json",
                    "X-KEY": settings.PROSPEO_API_KEY,
                },
                json={"first_name": first, "last_name": last, "company": domain},
            )
            if resp.status_code != 200:
                return None, "missing"
            payload = resp.json()
            if payload.get("error"):
                return None, "missing"
            inner = payload.get("response") or {}
            email = str(inner.get("email") or "").strip().lower()
            status = str(inner.get("email_status") or "").upper()
            if email and status == "VALID":
                return email, "verified"
    except Exception:
        return None, "missing"
    return None, "missing"


async def discover_verified_pattern_email(
    full_name: str,
    outlet_domain: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[str | None, str]:
    """Only return a pattern email if an external verifier confirms it as 'verified'."""
    settings = get_settings()
    if settings.EMAIL_VERIFIER_PROVIDER == "none" or not settings.EMAIL_VERIFIER_API_KEY:
        return None, "missing"

    candidates = infer_outlet_email_candidates(full_name, outlet_domain)
    if not candidates or check_mx_record(outlet_domain.strip().lower().removeprefix("www.")) is None:
        return None, "missing"

    for candidate in candidates:
        res = await verify_email_address(candidate, transport=transport)
        if res.status == "verified":
            return candidate, "verified"
    return None, "missing"


async def resolve_journalist_email(
    full_name: str,
    outlet_domain: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[str | None, str, str]:
    """Resolve a verified journalist email via Hunter -> Prospeo -> MX+Verifier pattern fallback."""
    email, status = await find_email_hunter(full_name, outlet_domain, transport=transport)
    if email and status == "verified":
        return email, "verified", "hunter"

    email, status = await find_email_prospeo(full_name, outlet_domain, transport=transport)
    if email and status == "verified":
        return email, "verified", "prospeo"

    email, status = await discover_verified_pattern_email(
        full_name, outlet_domain, transport=transport
    )
    if email and status == "verified":
        return email, "verified", "verified-pattern"

    return None, "missing", ""


