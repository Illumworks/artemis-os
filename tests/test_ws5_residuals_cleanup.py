from __future__ import annotations

import logging

from artemis.config import Settings
from artemis.marketing.writing_studio.collab.runtime_guard import (
    warn_if_multiworker_collab,
)


def test_multiworker_collab_guard_warns(caplog) -> None:
    with caplog.at_level(logging.WARNING):
        warn_if_multiworker_collab(2)
    assert "WS5 collab requires a single uvicorn worker in v1" in caplog.text
    assert "workers=2" in caplog.text


def test_multiworker_collab_guard_silent_at_one_worker(caplog) -> None:
    with caplog.at_level(logging.WARNING):
        warn_if_multiworker_collab(1)
    assert caplog.text == ""


def test_settings_reads_uvicorn_workers_from_web_concurrency_env(monkeypatch) -> None:
    monkeypatch.setenv("WEB_CONCURRENCY", "3")
    settings = Settings()
    assert settings.uvicorn_workers == 3
