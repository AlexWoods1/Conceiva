"""HTTP tests for the donor motility upload route."""

import re
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from app.models import Donor, MotilitySample, User
from app.motility_client import MotilityServiceError
from app.routes import _motility_comparison
from app.security import hash_password
from tests.test_http import _consent


def _create_donor(api) -> int:
    created = api.post(
        "/bank/donors/new",
        {
            "code": "DN-1",
            "blood_type": "O",
            "rh": "positive",
            "ancestry": "",
            "photo_key": "geo-1",
            "panel": "",
            "cmv": "unknown",
            "quarantine": "cleared",
            "family_limit": "4",
            "id_release_policy": "anonymous",
        },
    )
    return int(created.headers["location"].rsplit("/", 1)[-1])


def test_motility_upload_stores_the_result(api, monkeypatch):
    _consent(api, "bank@example.com", role="bank")
    donor_id = _create_donor(api)

    result = {
        "summary": {"total_motility_percent": 48.3, "percent_progressive": 34.2},
        "annotated_video_url": "/videos/abc/tracked.mp4",
        "who_reference": {
            "progressive_motility_min_pct": 30,
            "total_motility_min_pct": 42,
        },
        "disclaimer": "Research and education demo only.",
    }
    monkeypatch.setattr(
        "app.routes.analyze_donor_video", lambda video_bytes, filename, settings: result
    )

    response = api.client.post(
        f"/bank/donors/{donor_id}/motility",
        data={"csrf": api.csrf},
        files={"video": ("sample.mp4", b"fake video bytes", "video/mp4")},
    )
    assert response.status_code == 303

    page = api.get(f"/bank/donors/{donor_id}")
    body = page.json()
    assert body["motility_total_pct"] == 48.3
    assert body["motility_progressive_pct"] == 34.2

    db = api.session()
    try:
        donor = db.get(Donor, donor_id)
        assert (
            donor.motility_video_url == "http://localhost:8010/videos/abc/tracked.mp4"
        )
        assert donor.motility_sample_timing == "post_thaw"
        assert donor.motility_non_progressive_pct == pytest.approx(14.1)
        assert donor.motility_immotile_pct == pytest.approx(51.7)
        assert donor.motility_track_count is None
        sample = db.scalars(
            select(MotilitySample).where(MotilitySample.donor_id == donor_id)
        ).one()
        assert sample.timing == "post_thaw"
        assert sample.progressive_pct == pytest.approx(34.2)
        assert sample.mean_vcl is None
        assert sample.mean_vsl is None
        assert sample.fps is None
        assert donor.motility_review_status == "awaiting_review"
    finally:
        db.close()


def test_fresh_and_thaw_clips_stay_separate(api, monkeypatch):
    _consent(api, "bank@example.com", role="bank")
    donor_id = _create_donor(api)
    results = [
        {
            "summary": {
                "total_motility_percent": 60.0,
                "percent_progressive": 40.0,
                "num_tracks_analyzed": 12,
            },
            "annotated_video_url": "/videos/fresh/tracked.mp4",
        },
        {
            "summary": {
                "total_motility_percent": 30.0,
                "percent_progressive": 20.0,
                "num_tracks_analyzed": 9,
            },
            "annotated_video_url": "/videos/thaw/tracked.mp4",
        },
        {
            "summary": {
                "total_motility_percent": 55.0,
                "percent_progressive": 36.0,
                "num_tracks_analyzed": 11,
            },
            "annotated_video_url": "/videos/fresh-2/tracked.mp4",
        },
    ]

    def _analyze(_video_bytes, _filename, _settings):
        return results.pop(0)

    monkeypatch.setattr("app.routes.analyze_donor_video", _analyze)

    fresh = api.client.post(
        f"/bank/donors/{donor_id}/motility",
        data={"csrf": api.csrf},
        files={"fresh_video": ("fresh.mp4", b"fresh", "video/mp4")},
    )
    assert fresh.status_code == 303
    thawed = api.client.post(
        f"/bank/donors/{donor_id}/motility",
        data={"csrf": api.csrf},
        files={"thaw_video": ("thaw.mp4", b"thaw", "video/mp4")},
    )
    assert thawed.status_code == 303
    replaced = api.client.post(
        f"/bank/donors/{donor_id}/motility",
        data={"csrf": api.csrf},
        files={"fresh_video": ("fresh-2.mp4", b"fresh-2", "video/mp4")},
    )
    assert replaced.status_code == 303

    db = api.session()
    try:
        rows = {
            row.timing: row
            for row in db.scalars(
                select(MotilitySample).where(MotilitySample.donor_id == donor_id)
            )
        }
        assert set(rows) == {"fresh", "post_thaw"}
        assert rows["fresh"].progressive_pct == pytest.approx(36.0)
        assert rows["fresh"].track_count == 11
        assert rows["post_thaw"].progressive_pct == pytest.approx(20.0)
        assert rows["post_thaw"].track_count == 9
        assert (
            rows["post_thaw"].video_url
            == "http://localhost:8010/videos/thaw/tracked.mp4"
        )
        donor = db.get(Donor, donor_id)
        assert donor.motility_progressive_pct == pytest.approx(36.0)
        assert donor.motility_sample_timing == "fresh"
    finally:
        db.close()


def test_motility_upload_disabled_returns_clear_error(api, monkeypatch):
    api.app.state.settings = replace(
        api.app.state.settings, motility_uploads_enabled=False
    )
    _consent(api, "bank@example.com", role="bank")
    donor_id = _create_donor(api)

    def _fail(*_args, **_kwargs):
        raise AssertionError("analyze should not run when uploads are disabled")

    monkeypatch.setattr("app.routes.analyze_donor_video", _fail)
    response = api.client.post(
        f"/bank/donors/{donor_id}/motility",
        data={"csrf": api.csrf},
        files={"video": ("sample.mp4", b"fake video bytes", "video/mp4")},
    )
    assert response.status_code == 503
    assert "disabled" in response.json()["error"].lower()


def test_motility_upload_shows_service_error(api, monkeypatch):
    _consent(api, "bank@example.com", role="bank")
    donor_id = _create_donor(api)

    def _raise(*_args, **_kwargs):
        raise MotilityServiceError("Motility service is unreachable.")

    monkeypatch.setattr("app.routes.analyze_donor_video", _raise)

    response = api.client.post(
        f"/bank/donors/{donor_id}/motility",
        data={"csrf": api.csrf},
        files={"video": ("sample.mp4", b"fake video bytes", "video/mp4")},
    )
    assert response.status_code == 503
    assert "unreachable" in response.json()["error"]


def _upload(api, donor_id, monkeypatch, total, progressive):
    result = {
        "summary": {
            "total_motility_percent": total,
            "percent_progressive": progressive,
        },
        "annotated_video_url": "/videos/abc/tracked.mp4",
        "who_reference": {
            "progressive_motility_min_pct": 30,
            "total_motility_min_pct": 42,
        },
        "disclaimer": "Research and education demo only.",
    }
    monkeypatch.setattr(
        "app.routes.analyze_donor_video", lambda video_bytes, filename, settings: result
    )
    return api.client.post(
        f"/bank/donors/{donor_id}/motility",
        data={"csrf": api.csrf},
        files={"video": ("sample.mp4", b"fake video bytes", "video/mp4")},
    )


def test_a_result_above_both_who_limits_is_not_flagged(api, monkeypatch):
    _consent(api, "bank@example.com", role="bank")
    donor_id = _create_donor(api)

    _upload(api, donor_id, monkeypatch, total=48.3, progressive=34.2)

    db = api.session()
    try:
        assert db.get(Donor, donor_id).motility_below_reference is False
    finally:
        db.close()


def test_low_total_motility_flags_the_donor(api, monkeypatch):
    _consent(api, "bank@example.com", role="bank")
    donor_id = _create_donor(api)

    _upload(api, donor_id, monkeypatch, total=28.0, progressive=34.2)

    db = api.session()
    try:
        assert db.get(Donor, donor_id).motility_below_reference is True
    finally:
        db.close()


def test_low_progressive_motility_alone_flags_the_donor(api, monkeypatch):
    _consent(api, "bank@example.com", role="bank")
    donor_id = _create_donor(api)

    # Total clears its limit; progressive does not. Either one flags.
    _upload(api, donor_id, monkeypatch, total=50.0, progressive=12.0)

    db = api.session()
    try:
        assert db.get(Donor, donor_id).motility_below_reference is True
    finally:
        db.close()


def test_motility_upload_requires_a_file(api):
    _consent(api, "bank@example.com", role="bank")
    donor_id = _create_donor(api)

    response = api.post(f"/bank/donors/{donor_id}/motility", {})
    assert response.status_code == 400


def test_who_boundary_values_are_not_flagged(api, monkeypatch):
    # * Exact WHO lower reference limits are inclusive of the reference floor.
    _consent(api, "bank@example.com", role="bank")
    donor_id = _create_donor(api)

    _upload(api, donor_id, monkeypatch, total=42.0, progressive=30.0)

    db = api.session()
    try:
        assert db.get(Donor, donor_id).motility_below_reference is False
    finally:
        db.close()


def test_just_below_who_total_boundary_is_flagged(api, monkeypatch):
    _consent(api, "bank@example.com", role="bank")
    donor_id = _create_donor(api)

    _upload(api, donor_id, monkeypatch, total=41.9, progressive=30.0)

    db = api.session()
    try:
        assert db.get(Donor, donor_id).motility_below_reference is True
    finally:
        db.close()


def test_retention_is_sample_level_progressive_motility():
    fresh = MotilitySample(
        donor_id=1, timing="fresh", progressive_pct=33.3, total_pct=50
    )
    thawed = MotilitySample(
        donor_id=1, timing="post_thaw", progressive_pct=13.5, total_pct=20
    )
    comparison = _motility_comparison(fresh, thawed)
    assert comparison["retention_pct"] == pytest.approx(13.5 / 33.3 * 100)
    assert comparison["change_points"] == pytest.approx(-19.8)
    assert comparison["retention_label"] == "40.5%"
    assert comparison["change_label"] == "-19.8 percentage points"
    assert "cryosurvival_pct" not in comparison

    fresh.progressive_pct = 0
    comparison = _motility_comparison(fresh, thawed)
    assert "retention_pct" not in comparison
    assert comparison["change_label"] == "+13.5 percentage points"


def test_analyzer_kinematics_are_stored_and_review_can_be_recorded(api, monkeypatch):
    _consent(api, "bank@example.com", role="bank")
    donor_id = _create_donor(api)
    results = [
        {
            "summary": {
                "total_motility_percent": 55.0,
                "percent_progressive": 33.3,
                "percent_non_progressive": 21.7,
                "percent_immotile": 45.0,
                "num_tracks_analyzed": 80,
                "mean_vcl": 42.0,
                "mean_vsl": 28.5,
                "fps": 30.0,
                "scale_is_approximate": True,
                "cluster_count": 2,
            },
            "annotated_video_url": "/videos/fresh/tracked.mp4",
        },
        {
            "summary": {
                "total_motility_percent": 22.0,
                "percent_progressive": 13.5,
                "percent_non_progressive": 8.5,
                "percent_immotile": 78.0,
                "num_tracks_analyzed": 117,
                "mean_vcl": 18.2,
                "mean_vsl": 9.4,
                "fps": 29.97,
                "scale_is_approximate": True,
                "cluster_count": 1,
            },
            "annotated_video_url": "/videos/thaw/tracked.mp4",
        },
    ]

    def _analyze(_video_bytes, _filename, _settings):
        return results.pop(0)

    monkeypatch.setattr("app.routes.analyze_donor_video", _analyze)
    fresh = api.client.post(
        f"/bank/donors/{donor_id}/motility",
        data={"csrf": api.csrf},
        files={"fresh_video": ("fresh.mp4", b"fresh", "video/mp4")},
    )
    assert fresh.status_code == 303
    thawed = api.client.post(
        f"/bank/donors/{donor_id}/motility",
        data={"csrf": api.csrf},
        files={"thaw_video": ("thaw.mp4", b"thaw", "video/mp4")},
    )
    assert thawed.status_code == 303

    db = api.session()
    try:
        thaw = db.scalars(
            select(MotilitySample).where(
                MotilitySample.donor_id == donor_id,
                MotilitySample.timing == "post_thaw",
            )
        ).one()
        assert thaw.track_count == 117
        assert thaw.mean_vcl == pytest.approx(18.2)
        assert thaw.mean_vsl == pytest.approx(9.4)
        assert thaw.fps == pytest.approx(29.97)
        assert thaw.scale_is_approximate is True
        assert thaw.cluster_count == 1
        assert db.get(Donor, donor_id).motility_review_status == "awaiting_review"
    finally:
        db.close()

    accepted = api.post(
        f"/bank/donors/{donor_id}/motility/review", {"action": "accept"}
    )
    assert accepted.status_code == 303
    flagged = api.post(f"/bank/donors/{donor_id}/motility/review", {"action": "flag"})
    assert flagged.status_code == 303
    rerun = api.post(f"/bank/donors/{donor_id}/motility/review", {"action": "rerun"})
    assert rerun.status_code == 303

    db = api.session()
    try:
        donor = db.get(Donor, donor_id)
        assert donor.motility_review_status == "awaiting_review"
        assert db.scalars(
            select(MotilitySample).where(MotilitySample.donor_id == donor_id)
        ).all()
    finally:
        db.close()


def test_review_without_an_analysis_is_rejected(api):
    _consent(api, "bank@example.com", role="bank")
    donor_id = _create_donor(api)
    response = api.post(
        f"/bank/donors/{donor_id}/motility/review", {"action": "accept"}
    )
    assert response.status_code == 400
    assert "analysis" in response.json()["error"].lower()


def test_microscopy_page_shows_only_measured_lab_fields(make_app):
    app = make_app()
    db = app.state.session_factory()
    try:
        user = User(
            email="lab@example.com",
            password_hash=hash_password("password1"),
            role="bank",
            consent_at=datetime.now(timezone.utc),
        )
        db.add(user)
        db.flush()
        donor = Donor(
            bank_user_id=user.id,
            code="DN-240",
            blood_type="A",
            rh="positive",
            cmv="negative",
            quarantine="cleared",
        )
        db.add(donor)
        db.flush()
        db.add(
            MotilitySample(
                donor_id=donor.id,
                timing="fresh",
                total_pct=55.0,
                progressive_pct=33.3,
                non_progressive_pct=21.7,
                immotile_pct=45.0,
                track_count=80,
                mean_vcl=42.0,
                mean_vsl=28.5,
                fps=30.0,
                scale_is_approximate=True,
                cluster_count=2,
                video_url="",
            )
        )
        db.add(
            MotilitySample(
                donor_id=donor.id,
                timing="post_thaw",
                total_pct=22.0,
                progressive_pct=13.5,
                non_progressive_pct=8.5,
                immotile_pct=78.0,
                track_count=117,
                mean_vcl=18.2,
                mean_vsl=9.4,
                fps=29.97,
                scale_is_approximate=True,
                cluster_count=1,
                video_url="",
            )
        )
        db.commit()
        donor_id = donor.id
    finally:
        db.close()

    with TestClient(app, follow_redirects=False) as client:
        login = client.get("/login")
        token = re.search(r'name="csrf" value="([^"]+)"', login.text).group(1)
        signed = client.post(
            "/login",
            data={"csrf": token, "email": "lab@example.com", "password": "password1"},
        )
        assert signed.status_code == 303
        page = client.get(f"/bank/donors/{donor_id}")

    assert page.status_code == 200
    text = page.text
    assert "Software-first automated semen analysis" in text
    assert "Progressive motility retention" in text
    assert "33.3%" in text
    assert "13.5%" in text
    assert "40.5%" in text
    assert "-19.8 percentage points" in text
    assert "117" in text
    assert "Tracks analyzed" in text
    assert "Mean VCL" in text
    assert "Mean VSL" in text
    assert "Awaiting review" in text
    assert "Accept analysis" in text
    assert "Flag for manual review" in text
    assert "Cryosurvival" not in text
    assert "sperm tracked" not in text
    assert "Mean VAP" not in text
    assert "No clip yet" in text


def test_just_below_who_progressive_boundary_is_flagged(api, monkeypatch):
    _consent(api, "bank@example.com", role="bank")
    donor_id = _create_donor(api)

    _upload(api, donor_id, monkeypatch, total=42.0, progressive=29.9)

    db = api.session()
    try:
        assert db.get(Donor, donor_id).motility_below_reference is True
    finally:
        db.close()
