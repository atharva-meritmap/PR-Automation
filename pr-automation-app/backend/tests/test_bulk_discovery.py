import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.db.models import Client, User
from app.services.discovery import discover_journalists_for_client

SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0">
  <channel>
    <title>AI Systems News</title>
    <item>
      <title>Distributed AI Inference Scales to 1M RPS</title>
      <link>https://93.184.216.34/articles/ai-inference</link>
      <source url="https://93.184.216.34">TechDaily</source>
    </item>
    <item>
      <title>Next-Gen ML Compilers Benchmark</title>
      <link>https://93.184.216.34/articles/ml-compilers</link>
      <source url="https://93.184.216.34">CloudWeekly</source>
    </item>
  </channel>
</rss>
"""

ARTICLE_1_HTML = """
<!doctype html>
<html>
  <head><meta name="author" content="Maya Lin" /></head>
  <body>
    <p>Contact: <a href="mailto:maya.lin@example.com">maya.lin@example.com</a></p>
    <a href="https://www.linkedin.com/in/mayalin-tech">LinkedIn</a>
  </body>
</html>
"""

ARTICLE_2_HTML = """
<!doctype html>
<html>
  <head><meta name="author" content="Alex Rivera" /></head>
  <body>
    <p>Reach me at <a href="mailto:alex.rivera@example.com">alex.rivera@example.com</a></p>
  </body>
</html>
"""


@pytest.mark.asyncio
async def test_bulk_discover_journalists_for_candidate(db_session: AsyncSession) -> None:
    officer = User(
        email="officer.bulk@example.com",
        password_hash=hash_password("Pass1234!"),
        full_name="PR Officer",
        role="officer",
        is_active=True,
    )
    db_session.add(officer)
    await db_session.flush()

    atharva = Client(
        officer_id=officer.id,
        name="Atharva",
        domain="",
        industry="Distributed AI Systems",
        brand_voice="Built 1M RPS inference engine; EB-1A candidate",
        key_facts={"visa_category": "EB-1A", "role_title": "Staff ML Engineer", "company": "ScaleAI"},
    )
    db_session.add(atharva)
    await db_session.commit()
    await db_session.refresh(atharva)

    def handler(request: httpx.Request) -> httpx.Response:
        if "bing.com" in request.url.host:
            return httpx.Response(200, text=SAMPLE_RSS)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if request.url.path == "/articles/ai-inference":
            return httpx.Response(200, text=ARTICLE_1_HTML)
        if request.url.path == "/articles/ml-compilers":
            return httpx.Response(200, text=ARTICLE_2_HTML)
        return httpx.Response(404)

    discovered = await discover_journalists_for_client(
        db_session,
        atharva,
        max_results=5,
        transport=httpx.MockTransport(handler),
    )

    names = {j.canonical_name for j in discovered}
    assert {"Maya Lin", "Alex Rivera"}.issubset(names)
    maya = next(j for j in discovered if j.canonical_name == "Maya Lin")
    assert maya.email == "maya.lin@example.com"
    assert maya.email_status == "unverified"
    assert len(maya.articles) >= 1

