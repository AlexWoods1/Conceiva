"""Tests for donor childhood photos and the swipe deck's JSON shortlist calls."""

from __future__ import annotations

import sqlite3

from sqlalchemy import select
from starlette.testclient import TestClient

from app.constants import BABY_PHOTO_KEYS
from app.models import Donor
from tests.test_traits import DONOR_FIELDS, _couple_with_survey
from tests.test_http import _consent


def test_bank_saves_childhood_photo_and_rejects_unknown_keys(api):
    _consent(api, "bank@example.com", role="bank")
    created = api.post(
        "/bank/donors/new",
        {**DONOR_FIELDS, "code": "DN-B", "baby_photo_key": BABY_PHOTO_KEYS[0]},
    )
    assert created.status_code == 303
    blank = api.post("/bank/donors/new", {**DONOR_FIELDS, "code": "DN-N"})
    assert blank.status_code == 303
    db = api.session()
    try:
        photos = dict(db.execute(select(Donor.code, Donor.baby_photo_key)).all())
        assert photos == {"DN-B": BABY_PHOTO_KEYS[0], "DN-N": ""}
    finally:
        db.close()

    bad = api.post(
        "/bank/donors/new",
        {**DONOR_FIELDS, "code": "DN-X", "baby_photo_key": "../secret"},
    )
    assert bad.status_code == 400


def _donor_ids(api) -> list[int]:
    db = api.session()
    try:
        return list(db.scalars(select(Donor.id).order_by(Donor.id)))
    finally:
        db.close()


def test_shortlist_answers_json_for_the_swipe_deck(api):
    _consent(api, "bank@example.com", role="bank")
    api.post("/bank/donors/new", {**DONOR_FIELDS, "code": "DN-1"})
    api.post("/logout")
    _couple_with_survey(api)
    api.get("/match")
    donor_id = _donor_ids(api)[0]
    headers = {"Accept": "application/json"}

    added = api.client.post(
        f"/couple/shortlist/{donor_id}/add", data={"csrf": api.csrf}, headers=headers
    )
    assert added.status_code == 200
    assert added.json() == {
        "ok": True,
        "added": True,
        "message": "Added to shortlist.",
    }
    assert api.client.cookies.get("session")

    again = api.client.post(
        f"/couple/shortlist/{donor_id}/add", data={"csrf": api.csrf}, headers=headers
    )
    assert again.status_code == 200
    assert again.json()["ok"] is True
    assert again.json()["added"] is False

    missing = api.client.post(
        "/couple/shortlist/99999/add", data={"csrf": api.csrf}, headers=headers
    )
    assert missing.status_code == 400
    assert missing.json()["ok"] is False

    removed = api.client.post(
        f"/couple/shortlist/{donor_id}/remove",
        data={"csrf": api.csrf},
        headers=headers,
    )
    assert removed.json() == {"ok": True, "message": "Removed from shortlist."}

    form_post = api.post(f"/couple/shortlist/{donor_id}/add")
    assert form_post.status_code == 303
    assert form_post.headers["location"] == "/match"


def test_shortlist_session_survives_empty_db_rows(api):
    """Cookie shortlist rehydrates SQLite after a /tmp wipe on another instance."""
    _consent(api, "bank@example.com", role="bank")
    api.post("/bank/donors/new", {**DONOR_FIELDS, "code": "DN-1"})
    api.post("/logout")
    _couple_with_survey(api)
    api.get("/match")
    donor_id = _donor_ids(api)[0]
    headers = {"Accept": "application/json"}
    added = api.client.post(
        f"/couple/shortlist/{donor_id}/add", data={"csrf": api.csrf}, headers=headers
    )
    assert added.json()["added"] is True

    db = api.session()
    try:
        from sqlalchemy import delete

        from app.models import ShortlistItem

        db.execute(delete(ShortlistItem))
        db.commit()
    finally:
        db.close()

    page = api.get("/couple/shortlist")
    assert page.status_code == 200
    assert "DN-1" in page.text
    assert "No candidates yet" not in page.text


def test_home_shows_latest_donors_with_childhood_photos(make_app):
    app = make_app(seed_on_empty=True)
    with TestClient(app) as client:
        home = client.get("/")
    assert home.status_code == 200
    assert "Latest donors" in home.text
    for key in BABY_PHOTO_KEYS:
        assert f"/static/photos/{key}.jpg" in home.text
    assert "Hard stop" in home.text


def test_existing_database_gets_baby_photo_column(make_app, tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE donors (id INTEGER PRIMARY KEY, bank_user_id INTEGER, "
        "code VARCHAR(64))"
    )
    conn.commit()
    conn.close()

    make_app(database_path=path)

    conn = sqlite3.connect(path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(donors)")}
    conn.close()
    assert "baby_photo_key" in columns
