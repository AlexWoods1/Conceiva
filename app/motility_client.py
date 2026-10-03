"""Client for the separately-deployed motility analyzer service.

That service carries torch/ultralytics/opencv and takes 1-2 minutes per
video, so it cannot run inside this app's Vercel serverless deployment.
This module makes the HTTP call when a long-lived service is available,
and falls back to precomputed sample clips for local/hackathon demos.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

from app.config import PROJECT_ROOT, Settings

logger = logging.getLogger(__name__)

_DEMO_OK = PROJECT_ROOT / "samples" / "demo" / "summary.json"
_DEMO_LOW = PROJECT_ROOT / "samples" / "demo_low_motility" / "summary.json"
_STATIC_OK = "/static/motility/demo-tracked.mp4"
_STATIC_LOW = "/static/motility/demo-low-tracked.mp4"


def demo_motility_result(filename: str = "") -> dict:
    """Build a service-shaped payload from a precomputed sample clip.

    Args:
        filename: Upload name. Names containing ``low`` use the low-motility sample.

    Returns:
        Dict matching the motility service ``/analyze`` response, with an
        absolute-ready annotated video path under ``/static/motility/``.
    """
    use_low = "low" in Path(filename or "").stem.lower()
    summary_path = _DEMO_LOW if use_low else _DEMO_OK
    video_url = _STATIC_LOW if use_low else _STATIC_OK
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    return {
        "summary": payload["summary"],
        "annotated_video_url": video_url,
        "who_reference": payload["who_reference"],
        "disclaimer": payload.get(
            "disclaimer",
            "Research and education demo only.",
        ),
        "demo_fallback": True,
    }


def analyze_donor_video(
    video_bytes: bytes,
    filename: str,
    settings: Settings,
    client: httpx.Client | None = None,
) -> dict | None:
    """Sends a video to the motility service and returns its JSON result.

    Args:
        video_bytes: Raw video file contents.
        filename: Original filename, forwarded for content-type sniffing.
        settings: Process settings, for the service URL.
        client: Optional HTTP client. Tests pass a mock transport.

    Returns:
        The service's JSON response (summary, annotated_video_url,
        who_reference, disclaimer), or None if the call failed -- the
        service is often not running in dev/test since it needs heavy ML
        dependencies this app doesn't carry.
    """
    http = client or httpx.Client(timeout=300.0)
    close_client = client is None
    try:
        headers = {}
        if settings.motility_service_api_key:
            headers["X-Api-Key"] = settings.motility_service_api_key
        response = http.post(
            f"{settings.motility_service_url}/analyze",
            files={"video": (filename, video_bytes, "video/mp4")},
            headers=headers,
        )
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Motility analysis failed (%s).", type(exc).__name__)
        return None
    finally:
        if close_client:
            http.close()
