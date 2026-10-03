"""Run the motility FastAPI service for local Conceiva uploads.

Usage (from repo root):

    uv sync --group motility
    uv run --group motility python -m backend
"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    # * Local default: allow unauthenticated analyze unless a key is configured.
    if not os.environ.get("MOTILITY_SERVICE_API_KEY") and not os.environ.get(
        "MOTILITY_SERVICE_DEV"
    ):
        os.environ["MOTILITY_SERVICE_DEV"] = "1"
    uvicorn.run(
        "backend.main:app",
        host=os.environ.get("MOTILITY_HOST", "127.0.0.1"),
        port=int(os.environ.get("MOTILITY_PORT", "8010")),
        reload=False,
    )


if __name__ == "__main__":
    main()
