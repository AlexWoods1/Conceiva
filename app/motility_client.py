"""Client for the separately-deployed motility analyzer service.

That service carries torch/ultralytics/opencv and takes 1-2 minutes per
video, so it cannot run inside this app's Vercel serverless deployment.
Conceiva calls POST /analyze over HTTP when ``MOTILITY_SERVICE_URL`` is set.
"""

from __future__ import annotations

import logging

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


class MotilityServiceError(Exception):
    """Raised when the motility service cannot complete an analysis."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def analyze_donor_video(
    video_bytes: bytes,
    filename: str,
    settings: Settings,
    client: httpx.Client | None = None,
) -> dict:
    """Send a video to the motility service and return its JSON result.

    Args:
        video_bytes: Raw video file contents.
        filename: Original filename, forwarded for content-type sniffing.
        settings: Process settings, for the service URL and API key.
        client: Optional HTTP client. Tests pass a mock transport.

    Returns:
        The service's JSON response (summary, annotated_video_url,
        who_reference, disclaimer).

    Raises:
        MotilityServiceError: When the service is unreachable or returns an error.
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
        if response.status_code >= 400:
            detail = _error_detail(response)
            logger.warning(
                "Motility analysis failed (%s): %s",
                response.status_code,
                detail,
            )
            raise MotilityServiceError(detail)
        return response.json()
    except MotilityServiceError:
        raise
    except httpx.HTTPError as exc:
        logger.warning("Motility analysis failed (%s).", type(exc).__name__)
        raise MotilityServiceError(
            "Motility service is unreachable. Start it with: "
            "uv run --group motility uvicorn backend.main:app "
            "--host 127.0.0.1 --port 8010"
        ) from exc
    except ValueError as exc:
        logger.warning("Motility analysis returned invalid JSON.")
        raise MotilityServiceError(
            "Motility service returned an invalid response."
        ) from exc
    finally:
        if close_client:
            http.close()


def _error_detail(response: httpx.Response) -> str:
    """Pull a short error string from a failed motility service response."""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        detail = body.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
    if response.status_code == 401:
        return (
            "Motility service rejected the API key. Set the same "
            "MOTILITY_SERVICE_API_KEY on the app and the backend, or start "
            "the backend with MOTILITY_SERVICE_DEV=1."
        )
    if response.status_code == 413:
        return "Video is too large for motility analysis."
    return f"Motility analysis failed (HTTP {response.status_code})."
