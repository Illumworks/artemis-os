"""CLI entrypoint for the consolidated health report.

Examples::

    uv run python -m artemis.ops
"""

import sys

from artemis.ops.health import main

if __name__ == "__main__":
    sys.exit(main())
