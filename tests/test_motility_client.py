"""Tests for the motility service client."""

from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.motility_client import MotilityServiceError, analyze_donor_video


def _settings(**overrides) -> Settings:
    values = {
        "database_path": Path("unused.db"),
        "session_secret": "test",
        "llm_api_key": "",
        "llm_base_url": "https://llm.example/v1",
        "llm_model": "test-model",
        "enable_face_compare": False,
        "seed_on_empty": False,
        "motility_service_url": "https://motility.example",
    }
    values.update(overrides)
    return Settings(**values)


def test_analyze_donor_video_posts_the_file_and_returns_the_result():
    payload = {
        "summary": {"total_motility_percent": 48.3, "percent_progressive": 34.2},
        "annotated_video_url": "/videos/abc/tracked.mp4",
        "who_reference": {
            "progressive_motility_min_pct": 30,
            "total_motility_min_pct": 42,
        },
        "disclaimer": "Research and education demo only.",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://motility.example/analyze"
        assert b"sample.mp4" in request.content
        return httpx.Response(200, json=payload)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = analyze_donor_video(
            b"fake video bytes", "sample.mp4", _settings(), client
        )

    assert result == payload


def test_analyze_donor_video_raises_when_the_service_is_unreachable():
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(MotilityServiceError, match="unreachable"):
            analyze_donor_video(b"fake video bytes", "sample.mp4", _settings(), client)


def test_analyze_donor_video_raises_on_a_server_error():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "pipeline deps missing"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(MotilityServiceError, match="pipeline deps missing"):
            analyze_donor_video(b"fake video bytes", "sample.mp4", _settings(), client)


def test_analyze_donor_video_sends_api_key_header():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers.get("x-api-key")
        return httpx.Response(200, json={"summary": {}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        analyze_donor_video(
            b"x",
            "a.mp4",
            _settings(motility_service_api_key="secret-key"),
            client,
        )

    assert seen["key"] == "secret-key"
