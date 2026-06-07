"""Repository helpers for Writing Studio rules + scaffolding domain.

All functions are async and accept a SQLAlchemy AsyncSession.
Callers own commit / rollback.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.writing_rules.models import (
    WritingDraftThreadMessage,
    WritingExample,
    WritingFolder,
    WritingProfile,
    WritingRule,
    WritingSource,
    WritingTrainingCandidate,
)

# ── Profiles ──────────────────────────────────────────────────────────────────


async def list_profiles(
    session: AsyncSession, *, include_archived: bool = False
) -> list[WritingProfile]:
    q = select(WritingProfile).order_by(WritingProfile.created_at, WritingProfile.id)
    if not include_archived:
        q = q.where(WritingProfile.status != "archived")
    result = await session.execute(q)
    return list(result.scalars())


async def get_profile(session: AsyncSession, profile_id: int) -> WritingProfile | None:
    return await session.get(WritingProfile, profile_id)


async def get_active_profile(session: AsyncSession) -> WritingProfile | None:
    """Return first active profile (mirrors Node getFirstActiveProfile)."""
    result = await session.execute(
        select(WritingProfile)
        .where(WritingProfile.status == "active")
        .order_by(WritingProfile.id)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def create_profile(session: AsyncSession, **kwargs: Any) -> WritingProfile:
    profile = WritingProfile(**kwargs)
    session.add(profile)
    await session.flush()
    await session.refresh(profile)
    return profile


async def update_profile(
    session: AsyncSession, profile_id: int, **kwargs: Any
) -> WritingProfile | None:
    profile = await get_profile(session, profile_id)
    if profile is None:
        return None
    for key, value in kwargs.items():
        setattr(profile, key, value)
    profile.updated_at = datetime.now(UTC)
    await session.flush()
    return profile


# ── Folders ───────────────────────────────────────────────────────────────────


async def list_folders(session: AsyncSession, profile_id: int | None = None) -> list[WritingFolder]:
    # Exclude soft-deleted rows (campaign-derived folders that were explicitly deleted).
    q = (
        select(WritingFolder)
        .where(WritingFolder.deleted_at.is_(None))
        .order_by(WritingFolder.created_at, WritingFolder.id)
    )
    if profile_id is not None:
        q = q.where(WritingFolder.profile_id == profile_id)
    result = await session.execute(q)
    return list(result.scalars())


async def get_folder(session: AsyncSession, folder_id: int) -> WritingFolder | None:
    return await session.get(WritingFolder, folder_id)


async def get_folder_by_sync_id(session: AsyncSession, sync_id: str) -> WritingFolder | None:
    result = await session.execute(
        select(WritingFolder).where(WritingFolder.sync_id == sync_id).limit(1)
    )
    return result.scalar_one_or_none()


async def get_folder_by_campaign(session: AsyncSession, campaign_id: str) -> WritingFolder | None:
    """Return the first non-deleted folder whose campaign_id column matches the given value.

    ``campaign_id`` here is the TEXT key stored in ``writing_folders.campaign_id``.
    For per-candidate folders this will be ``str(candidate_id)``; for legacy
    family-level folders it will be the family string (e.g. ``"obc"``).

    Soft-deleted rows (deleted_at IS NOT NULL) are excluded so that a deleted
    campaign-derived folder is not resurfaced by the backfill or other callers.
    """
    result = await session.execute(
        select(WritingFolder)
        .where(WritingFolder.campaign_id == campaign_id, WritingFolder.deleted_at.is_(None))
        .order_by(WritingFolder.id)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_folder_by_candidate(session: AsyncSession, candidate_id: int) -> WritingFolder | None:
    """Return the per-campaign folder for a given candidate id, or None.

    Looks up ``writing_folders`` whose ``campaign_id`` column equals
    ``str(candidate_id)``.  This is the canonical lookup key for the new
    one-folder-per-campaign behaviour.
    """
    return await get_folder_by_campaign(session, str(candidate_id))


async def _get_folder_by_candidate_any(
    session: AsyncSession, candidate_id: int
) -> WritingFolder | None:
    """Return the folder row for a candidate including tombstoned rows.

    Unlike ``get_folder_by_candidate`` this function does NOT filter on
    ``deleted_at``, so it will return a soft-deleted (tombstoned) row.
    Used internally by ``get_or_create_folder_by_candidate`` to detect
    whether a tombstone exists before deciding to create a new folder.
    """
    result = await session.execute(
        select(WritingFolder)
        .where(WritingFolder.campaign_id == str(candidate_id))
        .order_by(WritingFolder.id)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_or_create_folder_by_candidate(
    session: AsyncSession,
    candidate_id: int,
    *,
    candidate_name: str | None = None,
) -> WritingFolder:
    """Return an existing per-candidate folder, or create one if none exists.

    The folder is keyed on ``str(candidate_id)`` stored in
    ``writing_folders.campaign_id``.  The folder's ``name`` is set once at
    creation time (as a snapshot) but is **always overridden at read/serialize
    time** by deriving it from the live ``CampaignCandidate.name``; callers
    should never rely on ``folder.name`` being current.

    ``candidate_name`` provides the initial snapshot name for the folder.
    When omitted the folder is named ``"Campaign {candidate_id}"``.

    The caller is responsible for flushing / committing.

    Note: this function DOES NOT respect tombstones — it will recreate a folder
    for a candidate even if one was previously soft-deleted.  Use
    ``get_or_create_folder_by_candidate_respecting_tombstone`` at call sites
    where the operator's explicit delete must be honoured (e.g. auto-assign
    paths that should not resurrect deleted folders).
    """
    folder = await get_folder_by_candidate(session, candidate_id)
    if folder is not None:
        return folder
    snapshot_name = candidate_name or f"Campaign {candidate_id}"
    folder = WritingFolder(
        name=snapshot_name,
        campaign_id=str(candidate_id),
    )
    session.add(folder)
    await session.flush()
    await session.refresh(folder)
    return folder


async def get_or_create_folder_by_candidate_respecting_tombstone(
    session: AsyncSession,
    candidate_id: int,
    *,
    candidate_name: str | None = None,
) -> WritingFolder | None:
    """Tombstone-aware folder resolution for auto-assign paths.

    Behaviour:
    - ACTIVE (non-tombstoned) folder exists for the candidate → return it.
    - TOMBSTONED folder exists for the candidate → return ``None``; do NOT
      create a new folder (respects the operator's explicit delete).
    - No folder row at all → create a fresh one (first-time behaviour).

    Use this function at every call site that might run automatically on page
    load or pipeline execution so that a deleted campaign folder is never
    silently resurrected.  The caller must handle ``None`` by skipping the
    ``folder_id`` stamp on the draft (draft stays in "All drafts").

    Callers that deliberately want to create-regardless (e.g. the
    ``/folders`` management route) should continue using
    ``get_or_create_folder_by_candidate``.

    The caller is responsible for flushing / committing.
    """
    # First check for ANY row (including tombstones).
    any_row = await _get_folder_by_candidate_any(session, candidate_id)
    if any_row is None:
        # No folder row at all — first-time create.
        snapshot_name = candidate_name or f"Campaign {candidate_id}"
        new_folder = WritingFolder(
            name=snapshot_name,
            campaign_id=str(candidate_id),
        )
        session.add(new_folder)
        await session.flush()
        await session.refresh(new_folder)
        return new_folder
    if any_row.deleted_at is not None:
        # Tombstoned — respect the delete, do not resurrect.
        return None
    # Active folder — return it.
    return any_row


async def get_or_create_folder_by_campaign(
    session: AsyncSession,
    campaign_id: str,
    *,
    name: str | None = None,
) -> WritingFolder:
    """Deprecated shim — prefer ``get_or_create_folder_by_candidate``.

    Retained for backward compatibility with callers that use the old
    family-string keying.  New code should always call
    ``get_or_create_folder_by_candidate`` with a numeric candidate id.
    """
    folder = await get_folder_by_campaign(session, campaign_id)
    if folder is not None:
        return folder
    folder = WritingFolder(
        name=name or campaign_id,
        campaign_id=campaign_id,
    )
    session.add(folder)
    await session.flush()
    await session.refresh(folder)
    return folder


async def create_folder(session: AsyncSession, **kwargs: Any) -> WritingFolder:
    folder = WritingFolder(**kwargs)
    session.add(folder)
    await session.flush()
    await session.refresh(folder)
    return folder


async def update_folder(
    session: AsyncSession, folder_id: int, **kwargs: Any
) -> WritingFolder | None:
    folder = await get_folder(session, folder_id)
    if folder is None:
        return None
    for key, value in kwargs.items():
        setattr(folder, key, value)
    folder.updated_at = datetime.now(UTC)
    await session.flush()
    return folder


async def delete_folder(
    session: AsyncSession,
    folder_id: int,
    *,
    clear_draft_folder_ids: bool = True,
) -> bool:
    """Delete a folder and (optionally) clear its drafts' metadata.folder_id.

    Strategy:
      - Campaign-derived folders (campaign_id IS NOT NULL): soft-deleted by
        stamping ``deleted_at``.  This prevents ``backfill_campaign_folders``
        from recreating the folder on the next overview load while preserving the
        row as a tombstone.
      - User-created folders (campaign_id IS NULL): hard-deleted (row removed).

    In both cases ``campaign_deliverables.deliverable_metadata.folder_id`` is
    cleared for every draft currently pointing at this folder so those drafts
    move to "All drafts" (lossless — the draft rows are never touched).

    ``clear_draft_folder_ids`` defaults to True; set False only in tests that
    do not need the draft-clearing side-effect.

    Returns True if the folder existed, False if not found.
    """
    from datetime import UTC, datetime

    from sqlalchemy import select

    from artemis.marketing.models import CampaignDeliverable

    folder = await session.get(WritingFolder, folder_id)
    if folder is None:
        return False

    if clear_draft_folder_ids:
        # Clear metadata.folder_id on every draft that referenced this folder.
        # Drafts are preserved (lossless); only their folder assignment is removed.
        result = await session.execute(select(CampaignDeliverable))
        for draft in result.scalars():
            meta = draft.deliverable_metadata
            if isinstance(meta, dict) and meta.get("folder_id") == folder_id:
                new_meta = dict(meta)
                new_meta.pop("folder_id", None)
                new_meta.pop("folder_name", None)
                draft.deliverable_metadata = new_meta
                draft.updated_at = datetime.now(UTC)
        await session.flush()

    if folder.campaign_id is not None:
        # Campaign-derived folder: soft-delete (tombstone) so backfill skips it.
        folder.deleted_at = datetime.now(UTC)
        folder.updated_at = datetime.now(UTC)
    else:
        # User-created folder: hard-delete (no tombstone needed).
        await session.delete(folder)

    await session.flush()
    return True


# ── Rules ─────────────────────────────────────────────────────────────────────


async def list_rules(
    session: AsyncSession,
    profile_id: int | None = None,
    rule_type: str | None = None,
    *,
    include_archived: bool = False,
) -> list[WritingRule]:
    q = select(WritingRule).order_by(WritingRule.created_at, WritingRule.id)
    if profile_id is not None:
        q = q.where(WritingRule.profile_id == profile_id)
    if rule_type is not None:
        q = q.where(WritingRule.rule_type == rule_type)
    if not include_archived:
        q = q.where(WritingRule.status != "archived")
    result = await session.execute(q)
    return list(result.scalars())


async def get_rule(session: AsyncSession, rule_id: int) -> WritingRule | None:
    return await session.get(WritingRule, rule_id)


async def get_rule_by_profile_type_title(
    session: AsyncSession,
    profile_id: int | None,
    rule_type: str,
    title: str,
) -> WritingRule | None:
    """Return the active rule matching (profile_id, rule_type, title).

    Mirrors Node's getRuleByProfileTypeTitle prepared statement.
    """
    q = (
        select(WritingRule)
        .where(
            WritingRule.profile_id == profile_id,
            WritingRule.rule_type == rule_type,
            WritingRule.title == title,
            WritingRule.status != "archived",
        )
        .limit(1)
    )
    result = await session.execute(q)
    return result.scalar_one_or_none()


async def create_rule(session: AsyncSession, **kwargs: Any) -> WritingRule:
    rule = WritingRule(**kwargs)
    session.add(rule)
    await session.flush()
    await session.refresh(rule)
    return rule


async def update_rule(session: AsyncSession, rule_id: int, **kwargs: Any) -> WritingRule | None:
    rule = await get_rule(session, rule_id)
    if rule is None:
        return None
    for key, value in kwargs.items():
        setattr(rule, key, value)
    rule.updated_at = datetime.now(UTC)
    await session.flush()
    return rule


async def delete_rule(session: AsyncSession, rule_id: int) -> bool:
    rule = await session.get(WritingRule, rule_id)
    if rule is None:
        return False
    await session.delete(rule)
    await session.flush()
    return True


# ── Examples ──────────────────────────────────────────────────────────────────


async def list_examples(
    session: AsyncSession,
    profile_id: int | None = None,
    example_type: str | None = None,
) -> list[WritingExample]:
    q = select(WritingExample).order_by(WritingExample.created_at, WritingExample.id)
    if profile_id is not None:
        q = q.where(WritingExample.profile_id == profile_id)
    if example_type is not None:
        q = q.where(WritingExample.example_type == example_type)
    result = await session.execute(q)
    return list(result.scalars())


async def get_example(session: AsyncSession, example_id: int) -> WritingExample | None:
    return await session.get(WritingExample, example_id)


async def get_example_by_profile_title_type(
    session: AsyncSession,
    profile_id: int | None,
    title: str,
    example_type: str,
) -> WritingExample | None:
    result = await session.execute(
        select(WritingExample)
        .where(
            WritingExample.profile_id == profile_id,
            WritingExample.title == title,
            WritingExample.example_type == example_type,
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def create_example(session: AsyncSession, **kwargs: Any) -> WritingExample:
    example = WritingExample(**kwargs)
    session.add(example)
    await session.flush()
    await session.refresh(example)
    return example


async def update_example(
    session: AsyncSession, example_id: int, **kwargs: Any
) -> WritingExample | None:
    example = await get_example(session, example_id)
    if example is None:
        return None
    for key, value in kwargs.items():
        setattr(example, key, value)
    example.updated_at = datetime.now(UTC)
    await session.flush()
    return example


async def delete_example(session: AsyncSession, example_id: int) -> bool:
    example = await session.get(WritingExample, example_id)
    if example is None:
        return False
    await session.delete(example)
    await session.flush()
    return True


# ── Sources ───────────────────────────────────────────────────────────────────


async def list_sources(session: AsyncSession, profile_id: int | None = None) -> list[WritingSource]:
    q = select(WritingSource).order_by(WritingSource.imported_at, WritingSource.id)
    if profile_id is not None:
        q = q.where(WritingSource.profile_id == profile_id)
    result = await session.execute(q)
    return list(result.scalars())


async def get_source(session: AsyncSession, source_id: int) -> WritingSource | None:
    return await session.get(WritingSource, source_id)


async def get_source_by_profile_key(
    session: AsyncSession, profile_id: int | None, source_key: str
) -> WritingSource | None:
    """Mirrors Node's getSourceByKey prepared statement."""
    result = await session.execute(
        select(WritingSource)
        .where(
            WritingSource.profile_id == profile_id,
            WritingSource.source_key == source_key,
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def create_source(session: AsyncSession, **kwargs: Any) -> WritingSource:
    source = WritingSource(**kwargs)
    session.add(source)
    await session.flush()
    await session.refresh(source)
    return source


async def update_source(
    session: AsyncSession, source_id: int, **kwargs: Any
) -> WritingSource | None:
    source = await get_source(session, source_id)
    if source is None:
        return None
    for key, value in kwargs.items():
        setattr(source, key, value)
    source.updated_at = datetime.now(UTC)
    await session.flush()
    return source


async def delete_source(session: AsyncSession, source_id: int) -> bool:
    source = await session.get(WritingSource, source_id)
    if source is None:
        return False
    await session.delete(source)
    await session.flush()
    return True


# ── Thread messages ───────────────────────────────────────────────────────────


async def create_thread_message(
    session: AsyncSession,
    draft_id: int,
    role: str,
    content: str,
    *,
    label: str | None = None,
    attachments: Any | None = None,
    trace: Any | None = None,
    engine: Any | None = None,
    prompt: Any | None = None,
) -> WritingDraftThreadMessage:
    """Persist one conversation turn for a draft.

    ``role`` must be ``"user"`` or ``"assistant"`` (not enforced at the repo
    layer — the compose endpoint owns that constraint).

    Mirrors Node's ``createWritingDraftThreadMessage`` in db/sqlite.js.
    Callers own commit / rollback.
    """
    msg = WritingDraftThreadMessage(
        draft_id=draft_id,
        role=role,
        label=label,
        content=content,
        attachments=attachments,
        trace=trace,
        engine=engine,
        prompt=prompt,
    )
    session.add(msg)
    await session.flush()
    await session.refresh(msg)
    return msg


async def list_thread_messages_for_draft(
    session: AsyncSession,
    draft_id: int,
) -> list[WritingDraftThreadMessage]:
    """Return all messages for a draft in chronological order (created_at ASC, id ASC).

    Mirrors Node's ``listThreadMessages`` prepared statement in db/sqlite.js.
    """
    result = await session.execute(
        select(WritingDraftThreadMessage)
        .where(WritingDraftThreadMessage.draft_id == draft_id)
        .order_by(WritingDraftThreadMessage.created_at, WritingDraftThreadMessage.id)
    )
    return list(result.scalars())


# ── Training candidates ───────────────────────────────────────────────────────


async def list_training_candidates(
    session: AsyncSession,
    *,
    status: str | None = None,
    profile_id: int | None = None,
) -> list[WritingTrainingCandidate]:
    """Return training candidates, newest first.

    Mirrors Node's GET /training-candidates query.
    Optionally filtered by status and/or profile_id.
    """
    q = select(WritingTrainingCandidate).order_by(
        WritingTrainingCandidate.created_at.desc(),
        WritingTrainingCandidate.id.desc(),
    )
    if status is not None:
        q = q.where(WritingTrainingCandidate.status == status)
    if profile_id is not None:
        q = q.where(WritingTrainingCandidate.profile_id == profile_id)
    result = await session.execute(q)
    return list(result.scalars())


async def create_training_candidate(
    session: AsyncSession,
    *,
    profile_id: int | None,
    draft_id: int | None,
    candidate_type: str = "rule",
    proposed_text: str,
    rationale: str | None = None,
    status: str = "proposed",
    scope: Any | None = None,
    source_version_id: int | None = None,
) -> WritingTrainingCandidate:
    """Create and flush a new training candidate. Caller commits.

    Mirrors Node's createWritingTrainingCandidate in db/sqlite.js.
    """
    candidate = WritingTrainingCandidate(
        profile_id=profile_id,
        draft_id=draft_id,
        candidate_type=candidate_type,
        proposed_text=proposed_text,
        rationale=rationale,
        status=status,
        scope_json=scope,
        source_version_id=source_version_id,
    )
    session.add(candidate)
    await session.flush()
    await session.refresh(candidate)
    return candidate


async def get_training_candidate(
    session: AsyncSession,
    candidate_id: int,
) -> WritingTrainingCandidate | None:
    """Fetch a single training candidate by PK."""
    return await session.get(WritingTrainingCandidate, candidate_id)


async def decide_training_candidate(
    session: AsyncSession,
    candidate_id: int,
    *,
    status: Literal["approved", "rejected"],
) -> WritingTrainingCandidate | None:
    """Flip a candidate's status and set decided_at. Caller commits.

    Returns None if the candidate is not found.
    Mirrors Node's POST /training-candidates/:id/decision endpoint logic.
    """
    candidate = await get_training_candidate(session, candidate_id)
    if candidate is None:
        return None
    candidate.status = status
    candidate.decided_at = func.now()
    await session.flush()
    await session.refresh(candidate)
    return candidate


def _compact_title(text: str, max_len: int = 72) -> str:
    """Return the first ``max_len`` chars with a trailing ellipsis if truncated.

    Matches Node's compactText(72) behaviour — single ``…`` char appended when
    the source text is longer than the limit.  No word-break: slice at exactly
    max_len and append the ellipsis (total length = max_len + 1).
    """
    if len(text) <= max_len:
        return text
    return text[:max_len] + "…"


async def promote_training_candidate(
    session: AsyncSession,
    candidate: WritingTrainingCandidate,
) -> WritingRule | WritingExample | None:
    """Promote an approved candidate to a WritingRule or WritingExample.

    Mirrors Node's ``promoteApprovedCandidate`` logic (writing-studio.js:124-144):
      - candidate_type == 'example' → creates a WritingExample (example_type='learned')
      - otherwise → creates a WritingRule (rule_type = candidate_type or 'voice',
        status='active')
    Title = first 72 chars of proposed_text (with trailing … if truncated).
    source_candidate_id is always set to candidate.id.

    Returns None if the candidate is not in 'approved' status.

    Idempotency note: WritingRule has a partial unique index on
    (profile_id, rule_type, title) where status != 'archived'.  WritingExample
    has a full unique constraint on (profile_id, title, example_type).  A second
    promotion with the same text will violate those constraints; the caller is
    responsible for catching IntegrityError if re-promotion is attempted.  This
    matches Node behaviour (it would create a duplicate row because the Node DB
    uses no unique index on writing_rules).
    """
    if candidate.status != "approved":
        return None

    title = _compact_title(candidate.proposed_text)
    profile_id = candidate.profile_id

    if candidate.candidate_type == "example":
        # Check if an identical example already exists to avoid unique constraint error.
        existing = await get_example_by_profile_title_type(session, profile_id, title, "learned")
        if existing is not None:
            return existing
        return await create_example(
            session,
            profile_id=profile_id,
            title=title,
            body=candidate.proposed_text,
            example_type="learned",
            source_candidate_id=candidate.id,
        )
    else:
        rule_type = candidate.candidate_type if candidate.candidate_type != "rule" else "voice"
        # Check for existing non-archived rule with same natural key.
        existing_rule = await get_rule_by_profile_type_title(session, profile_id, rule_type, title)
        if existing_rule is not None:
            return existing_rule
        return await create_rule(
            session,
            profile_id=profile_id,
            rule_type=rule_type,
            title=title,
            body=candidate.proposed_text,
            source_candidate_id=candidate.id,
            status="active",
        )
