"""Start the web application.

    python run.py            # listen on HOST:PORT from .env
    python run.py --reload   # auto-reload while developing
"""

from __future__ import annotations

import argparse

import uvicorn

from app.config import settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the bulk mailer web application.")
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("--reload", action="store_true", help="restart on code changes")
    args = parser.parse_args()

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="debug" if settings.debug else "info",
    )


if __name__ == "__main__":
    main()
