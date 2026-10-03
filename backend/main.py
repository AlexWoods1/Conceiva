"""Standalone FastAPI service wrapping the motility pipeline.

Runs separately from the SpermMatch app (which deploys to Vercel serverless
and can't carry torch/ultralytics/opencv or afford a multi-minute request).
SpermMatch calls POST /analyze over HTTP instead of importing this in-process.
"""

import hmac
import logging
import os
import shutil
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles

from backend.pipeline import analyze_video

logger = logging.getLogger(__name__)

OUT_DIR = Path("out/requests")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Every caller (including SpermMatch's own backend) must send this header.
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

app = FastAPI()
app.mount("/videos", StaticFiles(directory=OUT_DIR), name="videos")


def _check_api_key(x_api_key: str = Header(default="")):
    if API_KEY:
        if not hmac.compare_digest(x_api_key, API_KEY):
            raise HTTPException(status_code=401, detail="Invalid or missing API key.")
    elif not DEV_MODE:
        raise HTTPException(
            status_code=401, detail="Service is not configured for access."
        )


@app.post("/analyze", dependencies=[Depends(_check_api_key)])
async def analyze(video: UploadFile):
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

        result = analyze_video(str(input_path), out_dir=str(request_dir))
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
