"""HTTP tests for the donor motility upload route."""

from app.models import Donor
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

    db = api.session()
    try:
        donor = db.get(Donor, donor_id)
        # The service returns a path relative to ITS OWN origin, not this
        # app's -- a bare path stored verbatim 404s in the browser, since
        # <video src> resolves against the page's origin. Caught live by
        # running the app and the service on separate ports and actually
        # loading the result: percentages saved fine, player was broken.
        assert (
            donor.motility_video_url == "http://localhost:8010/videos/abc/tracked.mp4"
        )
    finally:
        db.close()

    page = api.get(f"/bank/donors/{donor_id}")
    body = page.json()
    assert body["motility_total_pct"] == 48.3
    assert body["motility_progressive_pct"] == 34.2


def test_motility_upload_shows_an_error_when_the_service_is_down(api, monkeypatch):
    _consent(api, "bank@example.com", role="bank")
    donor_id = _create_donor(api)

    monkeypatch.setattr(
        "app.routes.analyze_donor_video", lambda video_bytes, filename, settings: None
    )

    response = api.client.post(
        f"/bank/donors/{donor_id}/motility",
        data={"csrf": api.csrf},
        files={"video": ("sample.mp4", b"fake video bytes", "video/mp4")},
    )
    assert response.status_code == 503
    assert "unavailable" in response.json()["error"]


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
