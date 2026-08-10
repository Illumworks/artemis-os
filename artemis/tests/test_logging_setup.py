"""Guard the logging wiring.

The regression these protect against: `settings.log_level` existed and was set
in `.env` for months while nothing consumed it, so `artemis.*` INFO/DEBUG
records were discarded in production.
"""

import logging
from collections.abc import Iterator

import pytest

from artemis import logging_setup


@pytest.fixture(autouse=True)
def _restore_root_handlers() -> Iterator[None]:
    """Leave root's handler list exactly as we found it."""
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    original_artemis_level = logging.getLogger("artemis").level
    yield
    root.handlers[:] = original_handlers
    root.setLevel(original_level)
    logging.getLogger("artemis").setLevel(original_artemis_level)


def test_root_gets_a_real_handler_and_artemis_keeps_propagating() -> None:
    logging_setup.configure_logging()

    assert logging.getLogger().handlers, "root must own a handler, not lastResort"
    # propagate must stay True or pytest's caplog (a root handler) goes blind.
    assert logging.getLogger("artemis").propagate is True
    assert logging.getLogger("artemis").level == logging.INFO


def test_info_records_are_emitted_at_the_configured_level(caplog: pytest.LogCaptureFixture) -> None:
    """The actual bug: logger.info() on an artemis child logger must survive."""
    logging_setup.configure_logging()

    child = logging.getLogger("artemis.routes.integrations_slack_events")
    assert child.isEnabledFor(logging.INFO), "INFO must be enabled for artemis.*"

    with caplog.at_level(logging.INFO, logger="artemis.routes.integrations_slack_events"):
        child.info("route_inbound: dispatching %s", "test-event")
    assert "route_inbound: dispatching test-event" in caplog.text


def test_configure_logging_does_not_strip_pre_existing_root_handlers() -> None:
    """dictConfig would have removed caplog's handler; this must not."""
    sentinel = logging.NullHandler()
    logging.getLogger().addHandler(sentinel)

    logging_setup.configure_logging()

    assert sentinel in logging.getLogger().handlers


def test_configure_logging_is_idempotent_and_reports_the_level() -> None:
    before = len(logging.getLogger().handlers)
    first = logging_setup.configure_logging()
    second = logging_setup.configure_logging()

    assert first == second == "INFO"
    # Repeat calls must not stack duplicate stderr handlers (double-logging).
    assert len(logging.getLogger().handlers) <= before + 1


def test_noisy_libraries_are_pinned_to_warning() -> None:
    logging_setup.configure_logging()

    # Turning up artemis logging must not unleash SQLAlchemy statement logging.
    assert logging.getLogger("sqlalchemy.engine").level == logging.WARNING
    assert not logging.getLogger("httpx").isEnabledFor(logging.INFO)
