"""Tests for the standalone motility service's HTTP layer.

Mocks analyze_video() -- these test routing, auth, and size limits, not the
ML pipeline itself (that's ml/tests/ and backend's own curl-verified flow).
"""

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("MOTILITY_SERVICE_API_KEY", "test-key")
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    import backend.main as main

    importlib.reload(main)  # re-run module-level OUT_DIR/API_KEY setup under tmp_path

    def _fake_analyze(_video_path, out_dir):
        return {
            "summary": {"total_motility_percent": 50.0},
            "annotated_video_path": f"{out_dir}/tracked.mp4",
            "who_reference": {},
            "disclaimer": "test",
        }

    monkeypatch.setattr(main, "_load_analyze_video", lambda: _fake_analyze)
    (tmp_path / "out" / "requests").mkdir(parents=True, exist_ok=True)
    return TestClient(main.app)


def test_health_reports_auth_mode(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["auth"] == "api_key"


def test_rejects_requests_with_no_api_key(client):
    response = client.post("/analyze", files={"video": ("a.mp4", b"x", "video/mp4")})
    assert response.status_code == 401


def test_rejects_requests_with_the_wrong_api_key(client):
    response = client.post(
        "/analyze",
        headers={"X-Api-Key": "wrong"},
        files={"video": ("a.mp4", b"x", "video/mp4")},
    )
    assert response.status_code == 401


def test_accepts_requests_with_the_right_api_key(client):
    response = client.post(
        "/analyze",
        headers={"X-Api-Key": "test-key"},
        files={"video": ("a.mp4", b"x", "video/mp4")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["total_motility_percent"] == 50.0
    assert body["annotated_video_url"].startswith("/videos/")


def test_public_base_url_prefixes_annotated_video(monkeypatch, tmp_path):
    monkeypatch.setenv("MOTILITY_SERVICE_API_KEY", "test-key")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://203.0.113.10:8010")
    monkeypatch.chdir(tmp_path)
    import backend.main as main

    importlib.reload(main)

    def _fake_analyze(_video_path, out_dir):
        return {
            "summary": {"total_motility_percent": 50.0},
            "annotated_video_path": f"{out_dir}/tracked.mp4",
            "who_reference": {},
            "disclaimer": "test",
        }

    monkeypatch.setattr(main, "_load_analyze_video", lambda: _fake_analyze)
    (tmp_path / "out" / "requests").mkdir(parents=True, exist_ok=True)
    client = TestClient(main.app)
    response = client.post(
        "/analyze",
        headers={"X-Api-Key": "test-key"},
        files={"video": ("a.mp4", b"x", "video/mp4")},
    )
    assert response.status_code == 200
    assert response.json()["annotated_video_url"].startswith(
        "http://203.0.113.10:8010/videos/"
    )


def test_rejects_an_oversized_upload_and_cleans_up(client, tmp_path):
    import backend.main as main

    main.MAX_UPLOAD_BYTES = 10
    response = client.post(
        "/analyze",
        headers={"X-Api-Key": "test-key"},
        files={"video": ("a.mp4", b"x" * 1000, "video/mp4")},
    )
    assert response.status_code == 413
    assert list((tmp_path / "out" / "requests").iterdir()) == []


def test_sanitizes_the_uploaded_filename(client, tmp_path):
    response = client.post(
        "/analyze",
        headers={"X-Api-Key": "test-key"},
        files={"video": ("../../evil.html", b"x", "video/mp4")},
    )
    assert response.status_code == 200
    written = list((tmp_path / "out" / "requests").glob("*/input.mp4"))
    assert len(written) == 1
