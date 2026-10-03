"""Tests for donor physical traits: storage, display, and match filters."""

from __future__ import annotations

import sqlite3

from sqlalchemy import select

from app.models import Donor
from app.traits import (
    TraitFilter,
    donor_passes,
    format_height,
    format_weight,
    parse_trait_filter,
    trait_summary,
)
from tests.test_http import _consent

DONOR_FIELDS = {
    "blood_type": "O",
    "rh": "negative",
    "ancestry": "",
    "photo_key": "geo-1",
    "panel": "ACMG-SF",
    "cmv": "negative",
    "quarantine": "cleared",
    "family_limit": "10",
    "id_release_policy": "either",
}


def _donor(**traits) -> Donor:
    return Donor(code="DN-1", **traits)


def test_bmi_from_height_and_weight():
    assert _donor(height_cm=180, weight_kg=81).bmi == 25.0
    assert _donor(height_cm=180).bmi is None
    assert _donor(weight_kg=81).bmi is None


def test_display_formatting():
    assert format_height(180) == "180 cm (5'11\")"
    assert format_height(None) == ""
    assert format_weight(75) == "75 kg (165 lb)"
    donor = _donor(
        hair_color="brown", hair_type="wavy", eye_color="green", height_cm=178
    )
    assert trait_summary(donor) == ["Brown wavy hair", "green eyes", "178 cm (5'10\")"]
    assert trait_summary(_donor()) == []


def test_parse_trait_filter_ignores_bad_values():
    parsed = parse_trait_filter(
        {
            "hair_color": "Brown",
            "eye_color": "purple",
            "min_height_cm": "170",
            "max_height_cm": "999",
            "max_bmi": "abc",
        }
    )
    assert parsed == TraitFilter(hair_color="brown", min_height_cm=170)
    assert parsed.active
    assert not parse_trait_filter({}).active


def test_donor_passes_filters():
    donor = _donor(hair_color="brown", eye_color="green", height_cm=180, weight_kg=81)
    assert donor_passes(donor, TraitFilter())
    assert donor_passes(donor, TraitFilter(hair_color="brown", max_bmi=25.0))
    assert not donor_passes(donor, TraitFilter(eye_color="blue"))
    assert not donor_passes(donor, TraitFilter(min_height_cm=181))
    assert not donor_passes(donor, TraitFilter(max_height_cm=179))
    assert not donor_passes(donor, TraitFilter(max_bmi=24.9))
    assert not donor_passes(_donor(), TraitFilter(hair_type="curly"))
    assert not donor_passes(_donor(), TraitFilter(min_height_cm=150))


def test_bank_saves_traits_and_rejects_bad_values(api):
    _consent(api, "bank@example.com", role="bank")
    created = api.post(
        "/bank/donors/new",
        {
            **DONOR_FIELDS,
            "code": "DN-T",
            "hair_color": "brown",
            "hair_type": "wavy",
            "eye_color": "green",
            "height_cm": "180",
            "weight_kg": "81",
            "ethnicity": "  Northern   European ",
        },
    )
    assert created.status_code == 303
    db = api.session()
    try:
        donor = db.scalars(select(Donor).where(Donor.code == "DN-T")).one()
        assert (donor.hair_color, donor.hair_type, donor.eye_color) == (
            "brown",
            "wavy",
            "green",
        )
        assert (donor.height_cm, donor.weight_kg, donor.bmi) == (180, 81, 25.0)
        assert donor.ethnicity == "Northern European"
    finally:
        db.close()

    bad_color = api.post(
        "/bank/donors/new", {**DONOR_FIELDS, "code": "DN-X", "eye_color": "purple"}
    )
    assert bad_color.status_code == 400
    bad_height = api.post(
        "/bank/donors/new", {**DONOR_FIELDS, "code": "DN-Y", "height_cm": "90"}
    )
    assert bad_height.status_code == 400


def _couple_with_survey(api):
    _consent(api, "person@example.com")
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
    api.post(
        "/couple/survey/clinical",
        {"cmv_requirement": "any", "ancestry": "", "clinical_notes": ""},
    )
    api.post(
        "/couple/survey/preferences", {"id_release": "either", "family_limit": "1"}
    )


def test_match_list_filters_by_traits_without_changing_rank(api):
    _consent(api, "bank@example.com", role="bank")
    api.post(
        "/bank/donors/new",
        {
            **DONOR_FIELDS,
            "code": "DN-BLUE",
            "eye_color": "blue",
            "height_cm": "185",
            "weight_kg": "80",
        },
    )
    api.post(
        "/bank/donors/new",
        {**DONOR_FIELDS, "code": "DN-BROWN", "eye_color": "brown", "height_cm": "170"},
    )
    api.post("/bank/donors/new", {**DONOR_FIELDS, "code": "DN-BLANK"})
    api.post("/logout")

    _couple_with_survey(api)
    unfiltered = api.get("/match").json()["rows"]
    assert {row["code"] for row in unfiltered} == {"DN-BLUE", "DN-BROWN", "DN-BLANK"}
    scores = {row["code"]: row["score"] for row in unfiltered}

    blue = api.get("/match?eye_color=blue").json()["rows"]
    assert [row["code"] for row in blue] == ["DN-BLUE"]
    assert blue[0]["score"] == scores["DN-BLUE"]

    tall = api.get("/match?min_height_cm=175").json()["rows"]
    assert [row["code"] for row in tall] == ["DN-BLUE"]

    lean = api.get("/match?max_bmi=20").json()["rows"]
    assert lean == []


def test_existing_database_gets_trait_columns(make_app, tmp_path):
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
    assert {
        "motility_below_reference",
        "hair_color",
        "hair_type",
        "eye_color",
        "height_cm",
        "weight_kg",
        "ethnicity",
    } <= columns
