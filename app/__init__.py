"""Bulk mailer web application."""

import os

__version__ = "1.5.2"

# Images built by CI carry their exact build here (1.5.2-beta.3 from develop,
# 1.5.2-pr.12 from a pull request); see .github/workflows/docker.yml. The line
# above stays the one source of truth that tools/release.py and CI read.
__version__ = os.environ.get("BULK_MAILER_BUILD_VERSION", "").strip() or __version__
