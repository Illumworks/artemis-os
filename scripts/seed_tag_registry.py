"""Seed the Writing Studio tag registry into the configured Postgres database."""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from artemis.db import SessionLocal
from artemis.writing_rules.tag_registry_seed import seed_tag_registry_async


async def _main() -> None:
    async with SessionLocal() as session:
        assert isinstance(session, AsyncSession)
        await seed_tag_registry_async(session)
        await session.commit()
    print("Seeded Writing Studio tag registry.")


if __name__ == "__main__":
    asyncio.run(_main())
