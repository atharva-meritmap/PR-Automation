import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog, User


async def _login(client: AsyncClient, email: str, password: str) -> dict[str, str]:
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    tokens = resp.json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest.mark.asyncio
async def test_auth_login_refresh_and_me(client: AsyncClient, seeded_users: dict[str, User]) -> None:
    bad = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "wrong-password"},
    )
    assert bad.status_code == 401

    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "officer.a@example.com", "password": "OfficerAPass123!"},
    )
    assert resp.status_code == 200
    tokens = resp.json()

    me = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert me.status_code == 200
    assert me.json()["email"] == "officer.a@example.com"
    assert me.json()["role"] == "officer"

    refreshed = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["access_token"]


@pytest.mark.asyncio
async def test_rbac_admin_only_user_management(
    client: AsyncClient, seeded_users: dict[str, User]
) -> None:
    officer_headers = await _login(client, "officer.a@example.com", "OfficerAPass123!")
    admin_headers = await _login(client, "admin@example.com", "AdminPass123!")

    # Officer cannot list or create users
    assert (await client.get("/api/v1/users", headers=officer_headers)).status_code == 403
    assert (
        await client.post(
            "/api/v1/users",
            headers=officer_headers,
            json={
                "email": "new@example.com",
                "password": "Password123!",
                "full_name": "New Officer",
                "role": "officer",
            },
        )
    ).status_code == 403

    # Admin can create and list users
    created = await client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={
            "email": "new@example.com",
            "password": "Password123!",
            "full_name": "New Officer",
            "role": "officer",
        },
    )
    assert created.status_code == 201
    users_list = await client.get("/api/v1/users", headers=admin_headers)
    assert users_list.status_code == 200
    assert len(users_list.json()) == 4


@pytest.mark.asyncio
async def test_multi_tenant_client_isolation_and_audit_logs(
    client: AsyncClient,
    db_session: AsyncSession,
    seeded_users: dict[str, User],
) -> None:
    headers_a = await _login(client, "officer.a@example.com", "OfficerAPass123!")
    headers_b = await _login(client, "officer.b@example.com", "OfficerBPass123!")
    headers_admin = await _login(client, "admin@example.com", "AdminPass123!")

    # Officer A creates a client
    c_a = await client.post(
        "/api/v1/clients",
        headers=headers_a,
        json={
            "name": "Acme Robotics",
            "domain": "acmerobotics.com",
            "industry": "Robotics",
            "brand_voice": "Technical, concise",
            "key_facts": {"series_a": "$12M"},
        },
    )
    assert c_a.status_code == 201
    client_a_id = c_a.json()["id"]

    # Officer B creates a client
    c_b = await client.post(
        "/api/v1/clients",
        headers=headers_b,
        json={"name": "Beta Bio", "domain": "betabio.com"},
    )
    assert c_b.status_code == 201
    client_b_id = c_b.json()["id"]

    # Officer A only sees Acme Robotics
    list_a = await client.get("/api/v1/clients", headers=headers_a)
    assert list_a.status_code == 200
    assert [c["id"] for c in list_a.json()] == [client_a_id]

    # Officer A cannot read, update, or delete Officer B's client
    assert (await client.get(f"/api/v1/clients/{client_b_id}", headers=headers_a)).status_code == 403
    assert (
        await client.patch(
            f"/api/v1/clients/{client_b_id}",
            headers=headers_a,
            json={"name": "Hacked"},
        )
    ).status_code == 403
    assert (await client.delete(f"/api/v1/clients/{client_b_id}", headers=headers_a)).status_code == 403

    # Officer cannot reassign a client
    assert (
        await client.post(
            f"/api/v1/clients/{client_a_id}/reassign",
            headers=headers_a,
            json={"officer_id": str(seeded_users["officer_b"].id)},
        )
    ).status_code == 403

    # Admin sees both clients and can reassign client A to Officer B
    list_admin = await client.get("/api/v1/clients", headers=headers_admin)
    assert list_admin.status_code == 200
    assert len(list_admin.json()) == 2

    reassigned = await client.post(
        f"/api/v1/clients/{client_a_id}/reassign",
        headers=headers_admin,
        json={"officer_id": str(seeded_users["officer_b"].id)},
    )
    assert reassigned.status_code == 200
    assert reassigned.json()["officer_id"] == str(seeded_users["officer_b"].id)

    # Verify audit logs recorded
    logs = (await db_session.execute(select(AuditLog))).scalars().all()
    actions = {log.action for log in logs}
    assert {"auth.login", "client.create", "client.reassign"}.issubset(actions)
