"""Standalone FastAPI service wrapping the motility pipeline.

Runs separately from the SpermMatch app (which deploys to Vercel serverless
and can't carry torch/ultralytics/opencv or afford a multi-minute request).
SpermMatch calls POST /analyze over HTTP instead of importing this in-process.
"""

import shutil
import uuid
from pathlib import Path

from fastapi import FastAPI, UploadFile
from fastapi.staticfiles import StaticFiles

from backend.pipeline import analyze_video

OUT_DIR = Path("out/requests")
OUT_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI()
app.mount("/videos", StaticFiles(directory=OUT_DIR), name="videos")


@app.post("/analyze")
async def analyze(video: UploadFile):
    request_id = uuid.uuid4().hex
    request_dir = OUT_DIR / request_id
    request_dir.mkdir(parents=True)

    input_path = request_dir / video.filename
    with input_path.open("wb") as f:
        shutil.copyfileobj(video.file, f)

    result = analyze_video(str(input_path), out_dir=str(request_dir))
    annotated_name = Path(result["annotated_video_path"]).name

    return {
        "summary": result["summary"],
        "annotated_video_url": f"/videos/{request_id}/{annotated_name}",
        "who_reference": result["who_reference"],
        "disclaimer": result["disclaimer"],
    }
