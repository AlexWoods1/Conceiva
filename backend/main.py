"""Standalone FastAPI service wrapping the motility pipeline.

Runs separately from the SpermMatch app (which deploys to Vercel serverless
and can't carry torch/ultralytics/opencv or afford a multi-minute request).
SpermMatch calls POST /analyze over HTTP instead of importing this in-process.
"""

import logging
import os
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles

from backend.pipeline import analyze_video

logger = logging.getLogger(__name__)

OUT_DIR = Path("out/requests")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Every caller (including SpermMatch's own backend) must send this header.
# Unset in dev only -- this service does real CPU/GPU work per request and
# must not be left open to anyone who can reach it over the network.
API_KEY = os.environ.get("MOTILITY_SERVICE_API_KEY", "")
if not API_KEY:
    logger.warning("MOTILITY_SERVICE_API_KEY is not set; /analyze is unauthenticated.")

MAX_UPLOAD_BYTES = (
    200 * 1024 * 1024
)  # a 30s microscopy clip is a few MB; this is generous

app = FastAPI()
app.mount("/videos", StaticFiles(directory=OUT_DIR), name="videos")


def _check_api_key(x_api_key: str = Header(default="")):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")


@app.post("/analyze", dependencies=[Depends(_check_api_key)])
async def analyze(video: UploadFile):
    request_id = uuid.uuid4().hex
    request_dir = OUT_DIR / request_id
    request_dir.mkdir(parents=True)

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
    annotated_name = Path(result["annotated_video_path"]).name

    return {
        "summary": result["summary"],
        "annotated_video_url": f"/videos/{request_id}/{annotated_name}",
        "who_reference": result["who_reference"],
        "disclaimer": result["disclaimer"],
    }
