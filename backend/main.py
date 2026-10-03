"""Standalone FastAPI service wrapping the motility pipeline.

Runs separately from the Conceiva app (which deploys to Vercel serverless
and can't carry torch/ultralytics/opencv or afford a multi-minute request).
Conceiva calls POST /analyze over HTTP instead of importing this in-process.

Start locally (from the repo root):

    uv sync --group motility
    set MOTILITY_SERVICE_DEV=1
    uv run --group motility uvicorn backend.main:app --host 127.0.0.1 --port 8010
"""

from __future__ import annotations

import hmac
import logging
import os
import shutil
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

OUT_DIR = Path("out/requests")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Every caller (including Conceiva's own backend) must send this header.
# This service does real CPU/GPU work per request, so it fails CLOSED when
# unconfigured -- an empty/missing key must never mean "let everyone in".
# Local dev opts in explicitly instead of relying on an absent key.
API_KEY = os.environ.get("MOTILITY_SERVICE_API_KEY", "")
DEV_MODE = os.environ.get("MOTILITY_SERVICE_DEV", "").lower() in {"1", "true", "yes"}
if not API_KEY and not DEV_MODE:
    logger.warning(
        "MOTILITY_SERVICE_API_KEY is not set and MOTILITY_SERVICE_DEV is not "
        "enabled; /analyze will reject every request."
    )

MAX_UPLOAD_BYTES = (
    200 * 1024 * 1024
)  # a 30s microscopy clip is a few MB; this is generous

app = FastAPI(title="Conceiva Motility Service")
app.mount("/videos", StaticFiles(directory=OUT_DIR), name="videos")


def _check_api_key(x_api_key: str = Header(default="")):
    if API_KEY:
        if not hmac.compare_digest(x_api_key, API_KEY):
            raise HTTPException(status_code=401, detail="Invalid or missing API key.")
    elif not DEV_MODE:
        raise HTTPException(
            status_code=401, detail="Service is not configured for access."
        )


def _load_analyze_video():
    """Import the ML pipeline lazily so /health works before deps are installed."""
    try:
        from backend.pipeline import analyze_video
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Motility pipeline dependencies are missing. "
                "Install with: uv sync --group motility"
            ),
        ) from exc
    return analyze_video


@app.get("/health")
def health():
    """Liveness check for local wiring and deploy probes."""
    return {
        "status": "ok",
        "auth": "api_key" if API_KEY else ("dev" if DEV_MODE else "closed"),
    }


@app.post("/analyze", dependencies=[Depends(_check_api_key)])
async def analyze(video: UploadFile):
    """Run detection → tracking → motility metrics on one uploaded clip."""
    analyze_video = _load_analyze_video()
    request_id = uuid.uuid4().hex
    request_dir = OUT_DIR / request_id
    request_dir.mkdir(parents=True)

    try:
        suffix = Path(video.filename or "").suffix.lower()
        if suffix not in {".mp4", ".mov", ".webm", ".avi", ".mkv"}:
            suffix = ".mp4"
        input_path = request_dir / f"input{suffix}"
        written = 0
        with input_path.open("wb") as f:
            while chunk := await video.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="Video is too large.")
                f.write(chunk)
        if written == 0:
            raise HTTPException(status_code=400, detail="Empty video upload.")

        # * Pipeline is sync and CPU-heavy; keep the service event loop free.
        try:
            result = await run_in_threadpool(
                analyze_video, str(input_path), str(request_dir)
            )
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Motility pipeline failed for request %s", request_id)
            raise HTTPException(
                status_code=500,
                detail=f"Motility pipeline failed: {type(exc).__name__}: {exc}",
            ) from exc
    except Exception:
        shutil.rmtree(request_dir, ignore_errors=True)
        raise

    annotated_name = Path(result["annotated_video_path"]).name
    return {
        "summary": result["summary"],
        "annotated_video_url": f"/videos/{request_id}/{annotated_name}",
        "who_reference": result["who_reference"],
        "disclaimer": result["disclaimer"],
    }
