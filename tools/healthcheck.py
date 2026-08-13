"""Container health probe: exit 0 when the app answers on /healthz.

Kept as a script rather than an inline HEALTHCHECK command so it can read PORT
and stay readable. Uses only the standard library - the image has no curl.
"""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request

TIMEOUT = 3


def main() -> int:
    port = os.environ.get("PORT", "8000")
    url = f"http://127.0.0.1:{port}/healthz"
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
            if response.status == 200:
                return 0
            print(f"unexpected status {response.status} from {url}", file=sys.stderr)
    except (urllib.error.URLError, OSError) as exc:
        print(f"{url} unreachable: {exc}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
