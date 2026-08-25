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
import re
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

# ── Secret redaction ──────────────────────────────────────────────────────────
#
# Some vendors authenticate by putting the key in the URL query string. Vista
# Social's MCP endpoint is the first we integrate that does
# (`…/mcp?api_key=…`), and httpx logs every request line — method and full URL —
# at INFO. So the credential reaches any handler that sees httpx at INFO.
#
# `_NOISY_LIBRARIES` pins httpx to WARNING, which hides it today, but that is
# incidental protection, not a guarantee: raise httpx to INFO to debug one
# request and you write a live credential into `app.err.log`, and a script or
# cron that never calls `configure_logging()` has no pin at all.
#
# The filter below redacts the value instead of relying on the level. It is
# attached to the *logger*, so it runs before propagation and protects every
# handler downstream, pytest's caplog included. It removes nothing and silences
# nothing.

_SECRET_QUERY_PARAMS: tuple[str, ...] = ("api_key", "apikey", "access_token", "token", "key")

_REDACTION = "<redacted>"

_SECRET_QUERY_RE = re.compile(
    r"(?i)\b(" + "|".join(_SECRET_QUERY_PARAMS) + r")=([^&\s\"']+)",
)

# Loggers known to render outbound URLs.
_URL_LOGGING_LIBRARIES: tuple[str, ...] = ("httpx", "httpcore", "urllib3", "aiohttp.client")

_FILTER_FLAG = "_artemis_secret_redaction"


def redact_secrets(text: str) -> str:
    """Mask secret-bearing query parameters in ``text``."""
    return _SECRET_QUERY_RE.sub(lambda m: f"{m.group(1)}={_REDACTION}", text)


def _redact_arg(value: object) -> object:
    """Redact one log argument, whatever its type.

    Arguments are not always strings. httpx logs its request line as
    ``logger.info('HTTP Request: %s %s ...', method, request.url)`` where
    ``request.url`` is an ``httpx.URL`` *object* — an ``isinstance(value, str)``
    guard silently misses it, which is exactly how the first version of this
    filter let the key through.

    So redact on the rendered form and substitute only when that actually
    changed something. Numbers and other secret-free values keep their original
    type, so ``%d``-style format specifiers still work.
    """
    if isinstance(value, str):
        return redact_secrets(value)
    rendered = str(value)
    if "=" not in rendered:
        return value
    redacted = redact_secrets(rendered)
    return redacted if redacted != rendered else value


class SecretRedactingFilter(logging.Filter):
    """Strip credentials out of log records rather than dropping the records."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str) and "=" in record.msg:
            record.msg = redact_secrets(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: _redact_arg(v) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(_redact_arg(a) for a in record.args)
        return True


def install_secret_redaction() -> None:
    """Attach the redaction filter to URL-logging libraries. Idempotent.

    Safe to call from anywhere — scripts and jobs that never run the app's
    lifespan should call it before making authenticated requests.
    """
    for name in _URL_LOGGING_LIBRARIES:
        logger = logging.getLogger(name)
        if any(getattr(f, _FILTER_FLAG, False) for f in logger.filters):
            continue
        log_filter = SecretRedactingFilter()
        setattr(log_filter, _FILTER_FLAG, True)
        logger.addFilter(log_filter)


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

    # Defence in depth: the WARNING pin above hides credential-bearing URLs
    # today, but the redaction filter is what guarantees they never print.
    install_secret_redaction()

    return level
