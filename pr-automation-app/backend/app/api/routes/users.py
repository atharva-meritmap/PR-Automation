from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.core.security import hash_password
from app.db.models import User
from app.db.session import get_db
from app.schemas.user import UserCreate, UserRead, UserUpdate
from app.services.audit import record_audit_event

router = APIRouter(prefix="/api/v1/users", tags=["users"])


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> User:
    email_norm = payload.email.lower()
    existing = await db.execute(select(User).where(User.email == email_norm))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists",
        )

    user = User(
        email=email_norm,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        role=payload.role,
        is_active=True,
    )
    db.add(user)
    await db.flush()

    await record_audit_event(
        db,
        actor_user_id=admin.id,
        action="user.create",
        entity_type="user",
        entity_id=str(user.id),
        metadata={"role": user.role, "email": user.email},
        ip_address=request.client.host if request.client else None,
    )
    await db.commit()
    await db.refresh(user)
    return user


@router.get("", response_model=list[UserRead])
async def list_users(
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[User]:
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    return list(result.scalars().all())


@router.patch("/{user_id}", response_model=UserRead)
async def update_user(
    user_id: UUID,
    payload: UserUpdate,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> User:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if payload.full_name is not None:
        user.full_name = payload.full_name
    if payload.is_active is not None:
        user.is_active = payload.is_active
    if payload.role is not None:
        user.role = payload.role

    await record_audit_event(
        db,
        actor_user_id=admin.id,
        action="user.update",
        entity_type="user",
        entity_id=str(user.id),
        metadata=payload.model_dump(exclude_none=True),
        ip_address=request.client.host if request.client else None,
    )
    await db.commit()
    await db.refresh(user)
    return user

