"""Process-wide logging configuration.

Wires `settings.log_level` (env `ARTEMIS_LOG_LEVEL`) into Python's logging
module.  Until this existed the setting was defined and set but never consumed:
`artemis.*` loggers propagated to a handler-less root, so Python's
``lastResort`` handler emitted them at WARNING and every `logger.info` /
`logger.debug` in the codebase was silently discarded in production.

That cost real debugging time.  The Slack routing path logs its decision
points at debug level, so "no `route_inbound` in the log" was read as "the
event never arrived" when it actually meant "we cannot see this level."  Do
not remove the `configure_logging()` call in `main.lifespan` without replacing
it with something equivalent.

Records go to **stderr**, which the launchd plist points at
`~/Library/Logs/artemisos/app.err.log`.  Timestamps are included because
uvicorn's default formatter omits them, which makes after-the-fact forensics
on that file much harder than it needs to be.

Design note -- why this hand-rolls the config instead of calling
`logging.config.dictConfig`: dictConfig *replaces* root's handler list even
with ``disable_existing_loggers=False``.  Any test that starts the app (and so
runs `lifespan`) would therefore strip pytest's `caplog` handler off root for
the remainder of the session, silently blinding the 27 test modules that
assert on our log output.  Adding a handler idempotently is non-destructive and
safe to call from anywhere.
"""

import logging
import sys

from artemis.config import settings

_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Third-party loggers that are useful at WARNING but flood at INFO/DEBUG.
# `artemis.*` honours the configured level; these are pinned independently so
# turning on debug for our code does not bury it under library chatter.
_NOISY_LIBRARIES: tuple[str, ...] = (
    "aiosqlite",
    "apscheduler.executors.default",
    "apscheduler.scheduler",
    "asyncio",
    "httpcore",
    "httpx",
    "sqlalchemy.engine",
    "sqlalchemy.pool",
    "urllib3",
)

# Marks our handler so repeat calls update it instead of stacking duplicates.
_HANDLER_FLAG = "_artemis_stderr_handler"


def _install_root_handler() -> logging.Handler:
    """Add (or find) our stderr handler on root without disturbing others."""
    root = logging.getLogger()
    for handler in root.handlers:
        if getattr(handler, _HANDLER_FLAG, False):
            return handler

    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter(fmt=_FORMAT, datefmt=_DATE_FORMAT))
    setattr(handler, _HANDLER_FLAG, True)
    root.addHandler(handler)
    return handler


def configure_logging() -> str:
    """Install the logging config. Idempotent -- safe to call repeatedly.

    Returns the level that was applied, so callers can log/report it.
    """
    level = settings.log_level.upper()

    _install_root_handler()

    # Root stays at WARNING so unclaimed third-party loggers are quiet, while
    # `artemis.*` opts itself up to the configured level.  Ancestor levels are
    # not re-checked during propagation, so an INFO record from `artemis.foo`
    # still reaches root's handler.
    root = logging.getLogger()
    if root.level == logging.NOTSET or root.level > logging.WARNING:
        root.setLevel(logging.WARNING)

    # `artemis` keeps propagate=True (the default) on purpose: pytest's caplog
    # captures by handling on root, so cutting propagation would blind it.
    logging.getLogger("artemis").setLevel(level)

    for name in _NOISY_LIBRARIES:
        logging.getLogger(name).setLevel(logging.WARNING)

    return level
