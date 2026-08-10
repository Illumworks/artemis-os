"""Unit tests for the display_only guard in the pipeline scheduler.

Regression coverage for the Screen-Time Watch landmine: the seeded display
pipeline (artemis/screentime/pipeline_seed.py) carries a trigger_scheduled
node purely for pipelines-page visibility — the real work runs in a
dedicated cron runner (artemis/screentime/runner.py), NOT the shared
PipelineExecutor. Without a guard, ``start_pipeline_scheduler`` /
``register_pipeline_schedule`` would happily cron-execute the display
graph's no-op skill_call nodes and produce misleading "succeeded" runs.

Pure unit tests — no DB I/O, just plain Pipeline() instances.
"""

from __future__ import annotations

from artemis.pipelines.models import Pipeline
from artemis.pipelines.scheduler import _has_scheduled_trigger


def _pipeline(**kw) -> Pipeline:
    base = dict(
        id="test-pipeline",
        name="Test",
        nodes=[
            {"id": "trigger_scheduled", "type": "trigger_scheduled", "label": "t", "config": {}}
        ],
        edges=[],
        trigger_config={"type": "scheduled", "cron": "0 11 * * *", "timezone": "UTC"},
        status="active",
        metadata_=None,
    )
    base.update(kw)
    return Pipeline(**base)


def test_display_only_pipeline_is_skipped_even_with_scheduled_trigger():
    p = _pipeline(metadata_={"display_only": True, "isolated": True, "namespace": "screentime"})
    assert _has_scheduled_trigger(p) is False


def test_normal_pipeline_with_scheduled_trigger_is_registered():
    p = _pipeline(metadata_=None)
    assert _has_scheduled_trigger(p) is True


def test_normal_pipeline_with_other_metadata_but_no_display_only_flag():
    p = _pipeline(metadata_={"isolated": True})
    assert _has_scheduled_trigger(p) is True


def test_display_only_false_does_not_suppress_registration():
    p = _pipeline(metadata_={"display_only": False})
    assert _has_scheduled_trigger(p) is True


def test_pipeline_without_scheduled_trigger_node_is_never_registered():
    p = _pipeline(
        nodes=[{"id": "n1", "type": "skill_call", "label": "x", "config": {}}], metadata_=None
    )
    assert _has_scheduled_trigger(p) is False
