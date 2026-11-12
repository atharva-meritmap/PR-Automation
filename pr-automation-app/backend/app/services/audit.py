from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog


async def record_audit_event(
    db: AsyncSession,
    *,
    actor_user_id: UUID | None,
    action: str,
    entity_type: str,
    entity_id: str,
    metadata: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> AuditLog:
    """Append an immutable audit log entry inside the current transaction."""
    entry = AuditLog(
        actor_user_id=actor_user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        meta=metadata or {},
        ip_address=ip_address,
    )
    db.add(entry)
    return entry

