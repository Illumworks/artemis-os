import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

QUERY = """
SELECT agent_id, name, provider, model, fallback_provider, fallback_model
FROM agents
WHERE provider IS NULL OR provider = ''
   OR fallback_provider IS NULL OR fallback_provider = ''
   OR fallback_model IS NULL OR fallback_model = ''
ORDER BY agent_id
"""

async def main() -> None:
    from artemis.db import SessionLocal

    async with SessionLocal() as session:
        rows = (await session.execute(text(QUERY))).mappings().all()
    if not rows:
        print("agent provider audit: 0 violations")
        return
    print(f"agent provider audit: {len(rows)} violation(s)")
    for r in rows:
        print(f"- {r['agent_id']}: provider={r['provider']!r} fallback={r['fallback_provider']!r}/{r['fallback_model']!r}")

if __name__ == "__main__":
    asyncio.run(main())
