"""Templates route tests."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.writing_rules import repository as wr_repo
from artemis.writing_rules.seed_corpus import import_writing_seed_corpus


async def _create_active_profile(
    db_session: AsyncSession, name: str = "Templates Route Profile"
) -> int:
    profile = await wr_repo.create_profile(db_session, name=name, status="active")
    await db_session.commit()
    return profile.id


async def test_seeded_templates_endpoint_returns_parsed_rows(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    result = await import_writing_seed_corpus(db_session)
    await db_session.commit()

    response = await client.get("/api/writing-studio/templates?status=active")
    assert response.status_code == 200, response.text

    templates = response.json()
    assert len(templates) == result["templatesUpserted"] == 6

    template_a = next(template for template in templates if template["templateKey"] == "A")
    assert template_a["name"] == "15-second opener (Suite)"
    assert template_a["body"].startswith("Amira is a Learning Agent for Reading Growth.")
    assert template_a["status"] == "active"


async def test_template_routes_create_patch_retire_lossless(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _create_active_profile(db_session)

    create_response = await client.post(
        "/api/writing-studio/templates",
        json={
            "templateKey": "N1",
            "name": "Net-new template",
            "assetType": "email",
            "body": "Original template body.",
        },
    )
    assert create_response.status_code == 201, create_response.text
    created = create_response.json()
    assert created["status"] == "active"
    assert created["templateKey"] == "N1"

    patch_response = await client.patch(
        f"/api/writing-studio/templates/{created['id']}",
        json={"body": "Updated template body.", "assetType": "one-liner"},
    )
    assert patch_response.status_code == 200, patch_response.text
    patched = patch_response.json()
    assert patched["body"] == "Updated template body."
    assert patched["assetType"] == "one-liner"
    assert patched["status"] == "active"

    retire_response = await client.post(f"/api/writing-studio/templates/{created['id']}/retire")
    assert retire_response.status_code == 200, retire_response.text
    retired = retire_response.json()
    assert retired["status"] == "retired"

    get_response = await client.get(f"/api/writing-studio/templates/{created['id']}")
    assert get_response.status_code == 200, get_response.text
    persisted = get_response.json()
    assert persisted["id"] == created["id"]
    assert persisted["status"] == "retired"
    assert persisted["body"] == "Updated template body."


async def test_apply_template_creates_new_draft_with_seeded_content(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    profile_id = await _create_active_profile(db_session)
    template = await wr_repo.create_template(
        db_session,
        profile_id=profile_id,
        template_key="APL",
        name="Apply me",
        body="Template body for a new draft.",
    )
    await db_session.commit()

    apply_response = await client.post(
        f"/api/writing-studio/templates/{template.id}/apply",
        json={"title": "Draft from template"},
    )
    assert apply_response.status_code == 201, apply_response.text
    applied = apply_response.json()
    assert applied["title"] == "Draft from template"
    assert applied["status"] == "draft_ready"
    assert applied["content"] == "Template body for a new draft."

    draft_response = await client.get(f"/api/writing-studio/drafts/{applied['id']}")
    assert draft_response.status_code == 200, draft_response.text
    draft = draft_response.json()
    assert draft["title"] == "Draft from template"
    assert draft["content"] == "Template body for a new draft."
    assert draft["versions"][0]["content"] == "Template body for a new draft."
