from __future__ import annotations

import asyncio
from pathlib import Path

from artemis.config import settings
from artemis.db import SessionLocal
from artemis.integrations import repository as repo
from artemis.integrations.crypto import encrypt_credentials

CALLIE_ENV_PATH = Path(".env.callie")
CALLIE_AGENT_ID = "callie"
CALLIE_APP_ID = "A0B9Q790Y9Y"
CALLIE_BOT_USER_ID = "U0B9S32PTAM"
CALLIE_CAMPAIGN_SIGNALS_CHANNEL = "C0B9CHVC7KQ"


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'").strip('"')
    return values


def _required(values: dict[str, str], key: str) -> str:
    value = values.get(key, "").strip()
    if not value:
        raise ValueError(f"Missing required key in .env.callie: {key}")
    return value


async def _run() -> None:
    if not CALLIE_ENV_PATH.exists():
        raise FileNotFoundError(".env.callie not found")

    values = _parse_env_file(CALLIE_ENV_PATH)
    access_token = _required(values, "CALLIE_SLACK_BOT_TOKEN")
    signing_secret = _required(values, "CALLIE_SLACK_SIGNING_SECRET")
    client_secret = _required(values, "CALLIE_SLACK_CLIENT_SECRET")
    client_id = values.get("CALLIE_SLACK_CLIENT_ID", "157781284437.11330247032338").strip()
    bot_user_id = values.get("CALLIE_BOT_USER_ID", CALLIE_BOT_USER_ID).strip() or CALLIE_BOT_USER_ID

    async with SessionLocal() as session:
        slack_rows = await repo.list_active(session, provider="slack")
        artemis_row = next(
            (
                row
                for row in slack_rows
                if getattr(row, "agent_id", "default") in {"artemis", "default"}
            ),
            None,
        )
        if artemis_row is None:
            raise RuntimeError("No existing Artemis Slack integration found to derive workspace_id")

        allowed_channel_ids = [CALLIE_CAMPAIGN_SIGNALS_CHANNEL]
        marketing_campaigns_channel = settings.marketing_campaigns_slack_channel.strip()
        if marketing_campaigns_channel and marketing_campaigns_channel not in allowed_channel_ids:
            allowed_channel_ids.append(marketing_campaigns_channel)

        encrypted_credentials = encrypt_credentials(
            {
                "access_token": access_token,
                "token_type": "bot",
                "signing_secret": signing_secret,
                "client_id": client_id,
                "client_secret": client_secret,
                "bot_user_id": bot_user_id,
                "api_app_id": CALLIE_APP_ID,
            }
        )

        await repo.upsert_integration(
            session,
            provider="slack",
            workspace_id=str(artemis_row.workspace_id),
            agent_id=CALLIE_AGENT_ID,
            encrypted_credentials=encrypted_credentials,
            display_name="Calliope",
            bot_user_id=bot_user_id,
            scopes=list(getattr(artemis_row, "scopes", []) or []),
            metadata={
                "agent_id": CALLIE_AGENT_ID,
                "api_app_id": CALLIE_APP_ID,
                "allowed_channel_ids": allowed_channel_ids,
                "listen_channel_messages": True,
            },
        )
        await session.commit()

    CALLIE_ENV_PATH.unlink()
    print(
        "Imported Callie Slack credentials into integrations row for agent='callie' "
        f"and removed {CALLIE_ENV_PATH}"
    )


if __name__ == "__main__":
    asyncio.run(_run())
