import json
import re
from datetime import datetime, timedelta, timezone

import httpx

from app.core.config import get_settings


async def generate_openrouter_pitch(
    *,
    client_name: str,
    role_line: str,
    industry: str,
    approved_facts: str,
    target_title: str,
    target_publication: str,
    journalist_name: str = "",
    target_summary: str = "",
    transport: httpx.AsyncBaseTransport | None = None,
) -> str | None:
    """Draft a grounded PR pitch via OpenRouter free models; returns None on unavailability."""
    settings = get_settings()
    if not settings.OPENROUTER_API_KEY:
        return None

    greeting_hint = f"Address {journalist_name}" if journalist_name else "Use a brief professional greeting"
    prompt = (
        f"Write a concise, high-signal PR pitch email (under 140 words) from a PR team on behalf of "
        f"{client_name} ({role_line}, {industry}).\n"
        f"Target opportunity/article: \"{target_title}\" at {target_publication}.\n"
        f"Context: {target_summary[:400]}\n"
        f"STRICT RULE: Use ONLY the verified facts below—never invent awards, metrics, quotes, or employers:\n"
        f"{approved_facts[:800]}\n\n"
        f"{greeting_hint}. End with a clear offer for a brief interview or written quote. Output ONLY the email body."
    )

    candidate_models = [settings.OPENROUTER_MODEL]
    if "openrouter/free" not in candidate_models:
        candidate_models.append("openrouter/free")

    headers = {
        "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    url = f"{settings.OPENROUTER_BASE_URL.rstrip('/')}/chat/completions"

    async with httpx.AsyncClient(timeout=httpx.Timeout(18.0), transport=transport) as client:
        for model_id in candidate_models:
            try:
                resp = await client.post(
                    url,
                    headers=headers,
                    json={
                        "model": model_id,
                        "messages": [
                            {
                                "role": "system",
                                "content": "You are an expert PR strategist writing factual, zero-fluff media pitches.",
                            },
                            {"role": "user", "content": prompt},
                        ],
                        "max_tokens": 450,
                        "temperature": 0.4,
                    },
                )
                if resp.status_code != 200:
                    continue
                data = resp.json()
                content = (
                    data.get("choices", [{}])[0].get("message", {}).get("content") or ""
                ).strip()
                if content:
                    return str(content)
            except Exception:
                continue
    return None


def _snap_to_editorial_window(base_dt: datetime, days_ahead: int, hour_utc: int) -> datetime:
    """Ensure the curated follow-up lands on a Tue/Wed/Thu/Mon-Fri business editorial window."""
    clamped_days = max(2, min(days_ahead, 10))
    clamped_hour = max(13, min(hour_utc, 18))
    candidate = (base_dt + timedelta(days=clamped_days)).replace(
        hour=clamped_hour, minute=0, second=0, microsecond=0
    )
    # Avoid Saturday (5) and Sunday (6) -> move to Tuesday (next optimal editorial day)
    if candidate.weekday() == 5:
        candidate += timedelta(days=3)
    elif candidate.weekday() == 6:
        candidate += timedelta(days=2)
    return candidate


async def curate_follow_up_timing(
    *,
    journalist_name: str,
    outlet: str,
    beat: str,
    pitch_subject: str,
    now: datetime | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[datetime, str]:
    """Curate the highest-reply-probability follow-up date/time via OpenRouter (with Tue-Thu fallback)."""
    ref_now = now or datetime.now(timezone.utc)
    # Deterministic Tue/Wed/Thu default 4 days out at 14:00 UTC (9:00 AM ET)
    default_dt = _snap_to_editorial_window(ref_now, days_ahead=4, hour_utc=14)
    if default_dt.weekday() in (0, 4):
        # Prefer Tue/Wed/Thu over Mon/Fri when falling back deterministically
        default_dt += timedelta(days=1 if default_dt.weekday() == 0 else 4)
    default_reason = (
        f"AI-curated optimal editorial window ({default_dt.strftime('%a %b %d, %H:%M UTC')}) "
        f"targeting mid-week morning inbox triage for {outlet or 'journalist'}."
    )

    settings = get_settings()
    if not settings.OPENROUTER_API_KEY:
        return default_dt, default_reason

    prompt = (
        f"Today is {ref_now.strftime('%A, %Y-%m-%d %H:%M UTC')}.\n"
        f"We just pitched journalist {journalist_name} at {outlet or 'an outlet'} "
        f"(beat: {beat or 'general tech'}) with subject: \"{pitch_subject}\".\n"
        "Choose the single best follow-up timing to maximize reply probability (avoiding weekends, "
        "Monday morning inbox floods, and Friday afternoons). Respond ONLY with valid JSON:\n"
        '{"days_ahead": <int 2-10>, "send_hour_utc": <int 13-18>, "reason": "<1 short sentence>"}'
    )

    candidate_models = [settings.OPENROUTER_MODEL]
    if "openrouter/free" not in candidate_models:
        candidate_models.append("openrouter/free")

    headers = {
        "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    url = f"{settings.OPENROUTER_BASE_URL.rstrip('/')}/chat/completions"

    async with httpx.AsyncClient(timeout=httpx.Timeout(12.0), transport=transport) as client:
        for model_id in candidate_models:
            try:
                resp = await client.post(
                    url,
                    headers=headers,
                    json={
                        "model": model_id,
                        "messages": [
                            {
                                "role": "system",
                                "content": "You are a senior PR strategist optimizing journalist reply rates. Output JSON only.",
                            },
                            {"role": "user", "content": prompt},
                        ],
                        "max_tokens": 160,
                        "temperature": 0.2,
                    },
                )
                if resp.status_code != 200:
                    continue
                content = (
                    resp.json().get("choices", [{}])[0].get("message", {}).get("content") or ""
                ).strip()
                match = re.search(r"\{.*\}", content, re.DOTALL)
                if not match:
                    continue
                parsed = json.loads(match.group(0))
                days_ahead = int(parsed.get("days_ahead", 4))
                hour_utc = int(parsed.get("send_hour_utc", 14))
                reason = str(parsed.get("reason") or "").strip()
                curated_dt = _snap_to_editorial_window(ref_now, days_ahead, hour_utc)
                return curated_dt, reason or default_reason
            except Exception:
                continue

    return default_dt, default_reason

