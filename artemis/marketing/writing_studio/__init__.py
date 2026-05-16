"""Writing Studio integration — adapter, invoke, events, sync, external."""

from artemis.marketing.writing_studio.adapter import (
    init_adapter,
    process_event_with_session,
    reset_adapter,
)
from artemis.marketing.writing_studio.events import (
    DRAFT_EVENT_TYPES,
    DraftEvent,
    clear_subscribers,
    publish,
    subscribe,
)
from artemis.marketing.writing_studio.external import (
    ExternalApproval,
    ExternalDraft,
    ExternalWritingStudio,
    RealWritingStudio,
    StubWritingStudio,
    get_writing_studio,
)
from artemis.marketing.writing_studio.invoke import (
    ApprovalRecord,
    Draft,
    create_draft_from_candidate,
    list_campaign_asset_links,
    submit_draft_for_review,
)
from artemis.marketing.writing_studio.sync import (
    DraftSyncRecord,
    SyncConflict,
    SyncResult,
    WebhookIngestResult,
    export_deliverables_manifest,
    ingest_webhook,
    inspect_sync_state,
    push_draft_to_external,
)

__all__ = [
    # adapter
    "init_adapter",
    "reset_adapter",
    "process_event_with_session",
    # events
    "DRAFT_EVENT_TYPES",
    "DraftEvent",
    "subscribe",
    "publish",
    "clear_subscribers",
    # external
    "ExternalWritingStudio",
    "ExternalDraft",
    "ExternalApproval",
    "StubWritingStudio",
    "RealWritingStudio",
    "get_writing_studio",
    # invoke
    "Draft",
    "ApprovalRecord",
    "create_draft_from_candidate",
    "submit_draft_for_review",
    "list_campaign_asset_links",
    # sync
    "DraftSyncRecord",
    "SyncConflict",
    "SyncResult",
    "WebhookIngestResult",
    "push_draft_to_external",
    "export_deliverables_manifest",
    "ingest_webhook",
    "inspect_sync_state",
]
