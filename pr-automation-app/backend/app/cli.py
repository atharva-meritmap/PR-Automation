import asyncio
import sys

from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.models import User
from app.db.session import get_session_factory


async def seed_admin() -> int:
    settings = get_settings()
    if not settings.SEED_ADMIN_PASSWORD:
        print("SEED_ADMIN_PASSWORD must be set in environment", file=sys.stderr)
        return 1

    email_norm = settings.SEED_ADMIN_EMAIL.lower()
    factory = get_session_factory()
    async with factory() as db:
        existing = await db.execute(select(User).where(User.email == email_norm))
        if existing.scalar_one_or_none() is not None:
            print(f"Admin user {email_norm} already exists.")
            return 0

        admin = User(
            email=email_norm,
            password_hash=hash_password(settings.SEED_ADMIN_PASSWORD),
            full_name=settings.SEED_ADMIN_NAME,
            role="admin",
            is_active=True,
        )
        db.add(admin)
        await db.commit()
        print(f"Seeded admin user: {email_norm}")
        return 0


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "seed-admin":
        raise SystemExit(asyncio.run(seed_admin()))
    print("Usage: python -m app.cli seed-admin", file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    main()

