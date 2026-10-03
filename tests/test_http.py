"""HTTP tests for accounts, surveys, matches, and the bank catalog."""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import Carrier, CoupleProfile, CounselorProfile, Donor, LlmLog, User
from app.security import hash_password
from tests.conftest import TEST_PASSWORD

CATALOG = Path(__file__).parent / "fixtures" / "catalog.html"


def _user(api, email: str) -> User:
    db = api.session()
    try:
        return db.scalars(select(User).where(User.email == email)).one()
    finally:
        db.close()


def _register(api, email: str, role: str = "couple", password: str = TEST_PASSWORD):
    api.get("/login")
    return api.post("/register", {"email": email, "password": password, "role": role})


def _provision_privileged(
    api, email: str, role: str, password: str = TEST_PASSWORD, consent: bool = True
):
    """Insert a bank or counselor account (public register is couple-only)."""
    db = api.session()
    try:
        user = User(
            email=email,
            password_hash=hash_password(password),
            role=role,
            consent_at=datetime.now(timezone.utc) if consent else None,
        )
        db.add(user)
        db.flush()
        if role == "counselor":
            db.add(
                CounselorProfile(user_id=user.id, display_name=email.split("@", 1)[0])
            )
        db.commit()
    finally:
        db.close()
    api.get("/login")
    signed = api.post("/login", {"email": email, "password": password})
    assert signed.status_code == 303
    return signed


def _consent(api, email: str, role: str = "couple"):
    if role != "couple":
        return _provision_privileged(api, email, role, consent=True)
    response = _register(api, email, role)
    assert response.status_code == 303
    assert response.headers["location"] == "/consent"
    agreed = api.post("/consent", {"agree": "yes"})
    assert agreed.status_code == 303
    return agreed


def test_register_rejects_bad_input_and_stores_a_normalized_email(api):
    api.get("/login")
    too_long = ("a" * 251) + "@b.co"
    cases = [
        (
            {"email": "a@b.co", "password": TEST_PASSWORD, "role": "bank"},
            "Public signup is for couples only",
        ),
        (
            {"email": "a@b.co", "password": TEST_PASSWORD, "role": "counselor"},
            "Public signup is for couples only",
        ),
        (
            {"email": "a@b.co", "password": TEST_PASSWORD, "role": "admin"},
            "Public signup is for couples only",
        ),
        (
            {"email": "not-an-email", "password": TEST_PASSWORD, "role": "couple"},
            "Enter an email",
        ),
        (
            {"email": too_long, "password": TEST_PASSWORD, "role": "couple"},
            "Enter an email",
        ),
        (
            {"email": "a@b.co", "password": "short", "role": "couple"},
            "at least 8 characters",
        ),
    ]
    for form, needle in cases:
        response = api.post("/register", form)
        assert response.status_code == 400
        assert needle in response.json()["error"]

    created = api.post(
        "/register",
        {"email": "Person@Example.com", "password": TEST_PASSWORD, "role": "couple"},
    )
    assert created.status_code == 303
    assert _user(api, "person@example.com").email == "person@example.com"

    duplicate = api.post(
        "/register",
        {"email": "person@example.com", "password": TEST_PASSWORD, "role": "couple"},
    )
    assert duplicate.status_code == 400
    assert "already registered" in duplicate.json()["error"]


def test_login_logout_and_csrf(api):
    _register(api, "person@example.com")
    api.post("/logout")
    api.get("/login")

    rejected = api.client.post(
        "/login", data={"email": "person@example.com", "password": TEST_PASSWORD}
    )
    assert rejected.status_code == 400
    assert rejected.json()["detail"] == "CSRF check failed."

    mismatch = api.client.post(
        "/login",
        data={"csrf": "nope", "email": "person@example.com", "password": TEST_PASSWORD},
    )
    assert mismatch.status_code == 400

    wrong = api.post(
        "/login", {"email": "person@example.com", "password": "wrong-pass"}
    )
    assert wrong.status_code == 400
    assert "does not match" in wrong.json()["error"]

    signed_in = api.post(
        "/login", {"email": "person@example.com", "password": TEST_PASSWORD}
    )
    assert signed_in.status_code == 303
    assert signed_in.headers["location"] == "/consent"

    api.post("/consent", {"agree": "yes"})
    api.post("/logout")
    api.get("/login")
    signed_in_again = api.post(
        "/login", {"email": "Person@Example.com", "password": TEST_PASSWORD}
    )
    assert signed_in_again.headers["location"] == "/couple/history"

    logged_out = api.post("/logout")
    assert logged_out.status_code == 303
    assert logged_out.headers["location"] == "/"
    assert api.get("/account").headers["location"] == "/login"


def test_role_and_consent_guards(api):
    assert api.get("/couple/history").headers["location"] == "/login"
    assert api.get("/bank").headers["location"] == "/login"

    _register(api, "person@example.com", role="couple")
    assert api.get("/bank").headers["location"] == "/couple/history"
    assert api.get("/couple/history").headers["location"] == "/consent"
    declined = api.post("/consent", {"agree": "no"})
    assert declined.status_code == 400
    assert "Consent is required" in declined.json()["error"]

    api.post("/logout")
    _provision_privileged(api, "bank@example.com", role="bank", consent=False)
    assert api.get("/couple/history").headers["location"] == "/bank"
    assert api.get("/bank").headers["location"] == "/consent"


def test_account_delete_requires_confirmation(api):
    _consent(api, "person@example.com")
    page = api.get("/account")
    assert page.status_code == 200

    declined = api.post("/account/delete", {"confirm": "no"})
    assert declined.status_code == 400
    assert _user(api, "person@example.com").email == "person@example.com"

    removed = api.post("/account/delete", {"confirm": "yes"})
    assert removed.status_code == 303
    assert removed.headers["location"] == "/"
    db = api.session()
    try:
        assert (
            db.scalars(select(User).where(User.email == "person@example.com")).first()
            is None
        )
    finally:
        db.close()


def test_couple_survey_match_and_explanation(api):
    _consent(api, "bank@example.com", role="bank")
    created = api.post(
        "/bank/donors/new",
        {
            "code": "DN-100",
            "blood_type": "O",
            "rh": "negative",
            "ancestry": "Finnish",
            "photo_key": "geo-1",
            "panel": "ACMG-SF",
            "cmv": "negative",
            "quarantine": "cleared",
            "family_limit": "10",
            "id_release_policy": "open",
        },
    )
    assert created.status_code == 303
    api.post("/logout")

    _consent(api, "person@example.com")
    saved = api.post(
        "/couple/history",
        {
            "blood_type": "O",
            "rh": "negative",
            "prior_pregnancies": "1",
            "miscarriages": "0",
            "prior_donors": "none",
            "known_conditions": "none",
        },
    )
    assert saved.status_code == 303
    assert saved.headers["location"] == "/couple/carriers"
    assert api.get("/couple/carriers").json()["flash"] == "History saved."

    added = api.post(
        "/couple/carriers",
        {"gene": " cftr ", "zygosity": "heterozygous", "condition": "Cystic fibrosis"},
    )
    assert added.status_code == 303
    genes = api.get("/couple/carriers").json()["genes"]
    assert genes == ["CFTR"]

    invalid = api.post(
        "/couple/carriers", {"gene": "C F", "zygosity": "heterozygous", "condition": ""}
    )
    assert invalid.status_code == 400

    db = api.session()
    try:
        carrier_id = db.scalars(select(Carrier).where(Carrier.gene == "CFTR")).one().id
    finally:
        db.close()
    removed = api.post(f"/couple/carriers/{carrier_id}/delete")
    assert removed.status_code == 303
    missing = api.post("/couple/carriers/999/delete")
    assert missing.status_code == 404

    clinical = api.post(
        "/couple/survey/clinical",
        {"cmv_requirement": "any", "ancestry": "Finnish", "clinical_notes": "notes"},
    )
    assert clinical.headers["location"] == "/couple/survey/preferences"
    bad_cmv = api.post(
        "/couple/survey/clinical",
        {"cmv_requirement": "sometimes", "ancestry": "", "clinical_notes": ""},
    )
    assert bad_cmv.status_code == 400

    preferences = api.post(
        "/couple/survey/preferences", {"id_release": "either", "family_limit": "10"}
    )
    assert preferences.headers["location"] == "/match"
    bad_limit = api.post(
        "/couple/survey/preferences", {"id_release": "either", "family_limit": "0"}
    )
    assert bad_limit.status_code == 400

    listing = api.get("/match")
    assert listing.json()["rows"] == [
        {"code": "DN-100", "score": 100, "hard_stop": False}
    ]
    assert api.get("/match/999").status_code == 404

    db = api.session()
    try:
        donor_id = db.scalars(select(Donor).where(Donor.code == "DN-100")).one().id
    finally:
        db.close()
    explained = api.post(f"/match/{donor_id}/explain")
    assert explained.headers["location"] == f"/match/{donor_id}"
    detail = api.get(f"/match/{donor_id}")
    assert any("Blood types" in sentence for sentence in detail.json()["sentences"])
    db = api.session()
    try:
        assert (
            db.scalars(select(LlmLog).where(LlmLog.donor_id == donor_id)).first()
            is not None
        )
    finally:
        db.close()


def test_history_validation_rolls_back(api, monkeypatch):
    _consent(api, "person@example.com")
    invalid = api.post(
        "/couple/history",
        {
            "blood_type": "Z",
            "rh": "negative",
            "prior_pregnancies": "0",
            "miscarriages": "0",
            "prior_donors": "",
            "known_conditions": "",
        },
    )
    assert invalid.status_code == 400
    user_id = _user(api, "person@example.com").id
    db = api.session()
    try:
        assert db.get(CoupleProfile, user_id) is None
    finally:
        db.close()

    def explode(_value, _options, _label):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.routes._choice", explode)
    with pytest.raises(RuntimeError, match="boom"):
        api.post(
            "/couple/history",
            {
                "blood_type": "O",
                "rh": "negative",
                "prior_pregnancies": "0",
                "miscarriages": "0",
                "prior_donors": "",
                "known_conditions": "",
            },
        )
    db = api.session()
    try:
        assert db.get(CoupleProfile, user_id) is None
    finally:
        db.close()


def test_get_history_commits_the_empty_profile(api):
    _consent(api, "person@example.com")
    user_id = _user(api, "person@example.com").id
    assert api.get("/couple/history").status_code == 200
    db = api.session()
    try:
        profile = db.get(CoupleProfile, user_id)
        assert profile is not None
        assert profile.blood_type == ""
    finally:
        db.close()


def test_adult_photo_is_saved_only_when_face_compare_is_enabled(api, face_api):
    _consent(api, "person@example.com")
    api.post(
        "/couple/history",
        {
            "blood_type": "A",
            "rh": "positive",
            "prior_pregnancies": "0",
            "miscarriages": "0",
            "prior_donors": "",
            "known_conditions": "",
            "adult_photo_key": "adult-1",
        },
    )
    user_id = _user(api, "person@example.com").id
    db = api.session()
    try:
        assert db.get(CoupleProfile, user_id).adult_photo_key == ""
    finally:
        db.close()

    _consent(face_api, "person@example.com")
    ignored = face_api.post(
        "/couple/history",
        {
            "blood_type": "A",
            "rh": "positive",
            "prior_pregnancies": "0",
            "miscarriages": "0",
            "prior_donors": "",
            "known_conditions": "",
            "adult_photo_key": "not-a-key",
        },
    )
    assert ignored.status_code == 303
    face_user_id = _user(face_api, "person@example.com").id
    db = face_api.session()
    try:
        profile = db.get(CoupleProfile, face_user_id)
        assert profile.blood_type == "A"
        assert profile.adult_photo_key == ""
    finally:
        db.close()


def test_face_compare_keeps_an_allowed_portrait(face_api):
    _consent(face_api, "person@example.com")
    face_api.post(
        "/couple/history",
        {
            "blood_type": "B",
            "rh": "negative",
            "prior_pregnancies": "2",
            "miscarriages": "1",
            "prior_donors": "prior",
            "known_conditions": "known",
            "adult_photo_key": "adult-2",
        },
    )
    user_id = _user(face_api, "person@example.com").id
    db = face_api.session()
    try:
        profile = db.get(CoupleProfile, user_id)
        assert profile.adult_photo_key == "adult-2"
        assert profile.blood_type == "B"
    finally:
        db.close()


def test_bank_donor_crud_is_limited_to_the_owner(api):
    _consent(api, "bank@example.com", role="bank")
    invalid = api.post(
        "/bank/donors/new", {"code": "", "blood_type": "O", "rh": "negative"}
    )
    assert invalid.status_code == 400
    assert "donor code" in invalid.json()["error"]

    created = api.post(
        "/bank/donors/new",
        {
            "code": "DN-7",
            "blood_type": "AB",
            "rh": "positive",
            "ancestry": "Korean",
            "photo_key": "geo-3",
            "panel": "Panel",
            "cmv": "unknown",
            "quarantine": "cleared",
            "family_limit": "4",
            "id_release_policy": "anonymous",
        },
    )
    assert created.headers["location"].startswith("/bank/donors/")
    donor_id = int(created.headers["location"].rsplit("/", 1)[-1])
    edited = api.get(f"/bank/donors/{donor_id}")
    assert edited.json()["donor_code"] == "DN-7"

    added = api.post(
        f"/bank/donors/{donor_id}/carriers",
        {"gene": "HBB", "zygosity": "heterozygous", "condition": "Sickle cell"},
    )
    assert added.status_code == 303
    assert api.get(f"/bank/donors/{donor_id}").json()["genes"] == ["HBB"]
    bad_gene = api.post(
        f"/bank/donors/{donor_id}/carriers",
        {"gene": "!!", "zygosity": "heterozygous", "condition": ""},
    )
    assert bad_gene.status_code == 400

    api.post("/logout")
    _consent(api, "other@example.com", role="bank")
    assert api.get(f"/bank/donors/{donor_id}").status_code == 404
    assert api.post(f"/bank/donors/{donor_id}", {"code": "STOLEN"}).status_code == 404

    api.post("/logout")
    api.get("/login")
    api.post("/login", {"email": "bank@example.com", "password": TEST_PASSWORD})
    api.post("/consent", {"agree": "yes"})
    removed = api.post(f"/bank/donors/{donor_id}/delete")
    assert removed.headers["location"] == "/bank"
    assert api.get("/bank").json()["donor_codes"] == []


def test_catalog_sample_and_confirm(api, monkeypatch):
    monkeypatch.setattr("app.routes.FIXTURE_CATALOG", CATALOG)
    _consent(api, "bank@example.com", role="bank")

    parsed = api.post("/bank/catalog/sample")
    assert parsed.status_code == 303
    page = api.get("/bank/catalog")
    assert page.json()["flash"].startswith("Sample catalog parsed")
    assert page.json()["draft_codes"] == ["CAT-1", "CAT-2"]

    empty = api.post("/bank/catalog/confirm")
    assert empty.status_code == 400
    assert "at least one" in empty.json()["error"]

    saved = api.post("/bank/catalog/confirm", [("code", "CAT-1")])
    assert saved.status_code == 303
    bank = api.get("/bank")
    assert bank.json()["donor_codes"] == ["CAT-1"]
    assert api.get("/bank/catalog").json()["draft_codes"] == []

    db = api.session()
    try:
        donor = db.scalars(select(Donor).where(Donor.code == "CAT-1")).one()
        assert donor.catalog_source_url == ""
        assert donor.catalog_confirmed is True
        assert donor.photo_key
    finally:
        db.close()


def test_catalog_fetch_saves_the_source_url(api, monkeypatch):
    html = CATALOG.read_text(encoding="utf-8")

    def fake_fetch(url, _client):
        if "blocked" in url:
            raise ValueError("The catalog host disallows this path.")
        if url.endswith("/empty"):
            return "<p>none</p>"
        return html

    monkeypatch.setattr("app.routes.fetch_catalog_html", fake_fetch)
    _consent(api, "bank@example.com", role="bank")

    blocked = api.post(
        "/bank/catalog/fetch", {"url": "https://catalog.example/blocked"}
    )
    assert blocked.status_code == 400
    assert "disallows" in blocked.json()["error"]

    empty_page = api.post(
        "/bank/catalog/fetch", {"url": "https://catalog.example/empty"}
    )
    assert empty_page.status_code == 400
    assert "No donor records" in empty_page.json()["error"]

    fetched = api.post("/bank/catalog/fetch", {"url": "https://catalog.example/donors"})
    assert fetched.status_code == 303
    confirmed = api.post("/bank/catalog/confirm", [("code", "CAT-2")])
    assert confirmed.status_code == 303

    db = api.session()
    try:
        donor = db.scalars(select(Donor).where(Donor.code == "CAT-2")).one()
        assert donor.catalog_source_url == "https://catalog.example/donors"
    finally:
        db.close()
