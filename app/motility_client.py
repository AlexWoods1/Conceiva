"""Client for the separately-deployed motility analyzer service.

That service carries torch/ultralytics/opencv and takes 1-2 minutes per
video, so it cannot run inside this app's Vercel serverless deployment. This
module just makes the HTTP call and degrades gracefully when the service is
unreachable, same pattern as app/llm.py's explain().
"""

from __future__ import annotations

import logging

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


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
        response = http.post(
            f"{settings.motility_service_url}/analyze",
            files={"video": (filename, video_bytes, "video/mp4")},
        )
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Motility analysis failed (%s).", type(exc).__name__)
        return None
    finally:
        if close_client:
            http.close()
