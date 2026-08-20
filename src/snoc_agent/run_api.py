"""Entry point for the integrated SNOC dashboard API server.

Usage::

    python -m snoc_agent.run_api
    # or
    snoc-api
    # or with DB initialization:
    snoc-api --init-db

The server starts on http://localhost:8000 by default.  Dashboard endpoints are
available under ``/api/`` and the React frontend is served from the
``frontend/dist/`` directory.
"""

from __future__ import annotations

import argparse
import logging
import sys

from snoc_agent.logging_config import configure_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="SNOC integrated dashboard API")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    parser.add_argument(
        "--init-db", action="store_true", help="Initialize database tables before starting"
    )
    args = parser.parse_args()

    configure_logging()
    # Quiet noisy libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    logger = logging.getLogger("snoc_agent.api")

    if args.init_db:
        from snoc_agent.config import load_settings
        from snoc_agent.db.session import create_engine_and_session, create_schema

        settings = load_settings()
        engine, _ = create_engine_and_session(settings.database_url)
        create_schema(engine)
        logger.info("Database tables created for %s", settings.database_url)

    try:
        import uvicorn
    except ImportError:
        sys.exit(
            "uvicorn is required. Install with: "
            "pip install 'snoc-integrated-agent' or pip install uvicorn"
        )

    logger.info("Starting SNOC API on %s:%d", args.host, args.port)
    uvicorn.run(
        "snoc_agent.api.app:create_app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        factory=True,
    )


if __name__ == "__main__":
    main()
