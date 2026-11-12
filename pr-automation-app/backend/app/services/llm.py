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

