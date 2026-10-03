"""Tests for SQLite table round trips."""

from datetime import datetime, timezone

from app.models import Carrier, CoupleProfile, CoupleSurvey, Donor, LlmLog, PriorHistory, User
from app.security import hash_password


def test_rows_round_trip(db):
    user = User(
        email="couple@example.com",
        password_hash=hash_password("password1"),
        role="couple",
        consent_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    db.add(user)
    db.flush()
    db.add(CoupleProfile(user_id=user.id, blood_type="A", rh="positive", adult_photo_key="adult-1"))
    db.add(
        PriorHistory(
            user_id=user.id,
            prior_pregnancies=2,
            prior_donors="none",
            miscarriages=1,
            known_conditions="none",
        )
    )
    db.add(
        CoupleSurvey(
            user_id=user.id,
            ancestry="Finnish",
            cmv_requirement="negative_required",
            clinical_notes="notes",
            id_release="open",
            family_limit=10,
            clinical_done=True,
            preferences_done=True,
        )
    )
    donor = Donor(
        bank_user_id=user.id,
        code="DN-1",
        blood_type="O",
        rh="negative",
        ancestry="Korean",
        photo_key="geo-2",
        panel="ACMG-SF",
        cmv="negative",
        quarantine="cleared",
        family_limit=5,
        id_release_policy="open",
        catalog_source_url="https://example.com/catalog",
        catalog_confirmed=True,
    )
    db.add(donor)
    db.flush()
    db.add(
        Carrier(
            subject_type="donor",
            subject_id=donor.id,
            gene="CFTR",
            zygosity="heterozygous",
            condition="Cystic fibrosis",
        )
    )
    db.add(
        LlmLog(
            user_id=user.id,
            donor_id=donor.id,
            body="[]",
            created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
    )
    db.commit()

    assert db.get(User, user.id).role == "couple"
    assert db.get(CoupleProfile, user.id).blood_type == "A"
    assert db.get(PriorHistory, user.id).miscarriages == 1
    assert db.get(CoupleSurvey, user.id).preferences_done is True
    assert db.get(Donor, donor.id).catalog_source_url == "https://example.com/catalog"
    assert db.get(Carrier, 1).gene == "CFTR"
    assert db.get(LlmLog, 1).body == "[]"
