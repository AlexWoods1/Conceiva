"""Tests for account, survey, and match persistence."""

from sqlalchemy import func, select

from app.config import Settings
from app.llm import CitedSentence
from app.matching import confirmed_genes
from app.models import (
    Carrier,
    CoupleProfile,
    CoupleSurvey,
    Donor,
    LlmLog,
    PriorHistory,
    User,
)
from app.phenotype import resemblance_score
from app.security import hash_password
from app.services import (
    delete_account,
    find_ranked_donor,
    get_or_create_history,
    get_or_create_profile,
    get_or_create_survey,
    latest_explanation,
    list_carriers,
    packet_for,
    ranked_matches,
    store_explanation,
)


def _settings(**overrides) -> Settings:
    from pathlib import Path

    values = {
        "database_path": Path("unused.db"),
        "session_secret": "test",
        "llm_api_key": "",
        "llm_base_url": "https://llm.example/v1",
        "llm_model": "test-model",
        "enable_face_compare": False,
        "seed_on_empty": False,
    }
    values.update(overrides)
    return Settings(**values)


def _user(db, email, role="couple") -> User:
    user = User(email=email, password_hash=hash_password("password1"), role=role)
    db.add(user)
    db.commit()
    return user


def _ready_survey(db, user_id: int) -> CoupleSurvey:
    survey = get_or_create_survey(db, user_id)
    survey.ancestry = "Finnish"
    survey.cmv_requirement = "any"
    survey.id_release = "either"
    survey.family_limit = 10
    survey.preferences_done = True
    db.commit()
    return survey


def _donor(db, bank_id: int, code: str, **overrides) -> Donor:
    values = {
        "bank_user_id": bank_id,
        "code": code,
        "blood_type": "O",
        "rh": "negative",
        "ancestry": "Korean",
        "photo_key": "geo-1",
        "panel": "ACMG-SF",
        "cmv": "negative",
        "quarantine": "cleared",
        "family_limit": 10,
        "id_release_policy": "open",
        "catalog_confirmed": True,
    }
    values.update(overrides)
    donor = Donor(**values)
    db.add(donor)
    db.commit()
    return donor


def test_get_or_create_rows_are_stable(db):
    user = _user(db, "couple@example.com")

    assert (
        get_or_create_profile(db, user.id).user_id
        == get_or_create_profile(db, user.id).user_id
    )
    assert get_or_create_history(db, user.id).prior_pregnancies == 0
    assert get_or_create_survey(db, user.id).cmv_requirement == "any"
    assert get_or_create_survey(db, user.id).preferences_done is False


def test_list_carriers_orders_by_gene(db):
    user = _user(db, "couple@example.com")
    db.add_all(
        [
            Carrier(
                subject_type="couple",
                subject_id=user.id,
                gene="HBB",
                zygosity="heterozygous",
            ),
            Carrier(
                subject_type="couple",
                subject_id=user.id,
                gene="CFTR",
                zygosity="heterozygous",
            ),
            Carrier(
                subject_type="donor",
                subject_id=user.id,
                gene="CFTR",
                zygosity="unknown",
            ),
        ]
    )
    db.commit()

    assert [row.gene for row in list_carriers(db, "couple", user.id)] == ["CFTR", "HBB"]


def test_ranked_matches_waits_for_preferences_and_confirmed_donors(db):
    couple = _user(db, "couple@example.com")
    bank = _user(db, "bank@example.com", role="bank")
    hidden = _donor(db, bank.id, "HIDDEN", catalog_confirmed=False)
    visible = _donor(db, bank.id, "VISIBLE")
    settings = _settings()

    assert ranked_matches(db, couple, settings) == []
    survey = get_or_create_survey(db, couple.id)
    survey.preferences_done = True
    survey.family_limit = None
    db.commit()
    assert ranked_matches(db, couple, settings) == []

    _ready_survey(db, couple.id)
    rows = ranked_matches(db, couple, settings)

    assert [donor.code for donor, _result in rows] == ["VISIBLE"]
    assert hidden.code == "HIDDEN"
    assert rows[0][1].score == 100
    assert rows[0][1].face_score is None


def test_ranked_matches_sorts_hard_stops_last_and_can_carry_a_face_score(db):
    couple = _user(db, "couple@example.com")
    bank = _user(db, "bank@example.com", role="bank")
    profile = get_or_create_profile(db, couple.id)
    profile.blood_type = "O"
    profile.rh = "negative"
    profile.adult_photo_key = "adult-1"
    db.add(
        Carrier(
            subject_type="couple",
            subject_id=couple.id,
            gene="CFTR",
            zygosity="heterozygous",
        )
    )
    clear = _donor(db, bank.id, "DN-CLEAR", photo_key="geo-1")
    stopped = _donor(db, bank.id, "DN-STOP", photo_key="geo-2")
    db.add(
        Carrier(
            subject_type="donor",
            subject_id=stopped.id,
            gene="CFTR",
            zygosity="heterozygous",
        )
    )
    _ready_survey(db, couple.id)

    rows = ranked_matches(db, couple, _settings(enable_face_compare=True))

    assert [donor.code for donor, _result in rows] == ["DN-CLEAR", "DN-STOP"]
    assert rows[0][1].hard_stop is False
    assert rows[1][1].hard_stop is True
    assert rows[1][1].score == 0
    assert rows[0][1].face_score == resemblance_score("geo-1", "adult-1", True)
    assert find_ranked_donor(rows, stopped.id)[0].code == "DN-STOP"
    assert find_ranked_donor(rows, 999) is None
    assert clear.photo_key == "geo-1"


def test_packet_for_omits_unsaved_history_and_includes_saved_zeros(db):
    couple = _user(db, "couple@example.com")
    bank = _user(db, "bank@example.com", role="bank")
    donor = _donor(db, bank.id, "DN-1")
    _ready_survey(db, couple.id)
    result = ranked_matches(db, couple, _settings())[0][1]

    missing = packet_for(db, couple, donor, result)
    assert "history.prior_pregnancies" in missing.missing

    history = get_or_create_history(db, couple.id)
    history.prior_pregnancies = 0
    history.miscarriages = 0
    history.known_conditions = "call 415-555-0199"
    db.commit()
    present = packet_for(db, couple, donor, result)
    fields = {fact.field: fact.value for fact in present.facts}

    assert fields["history.prior_pregnancies"] == "0"
    assert fields["history.miscarriages"] == "0"
    assert "redacted-phone" in fields["history.known_conditions"]
    assert confirmed_genes([]) == frozenset()


def test_explanations_return_the_newest_log(db):
    couple = _user(db, "couple@example.com")
    bank = _user(db, "bank@example.com", role="bank")
    donor = _donor(db, bank.id, "DN-1")
    assert latest_explanation(db, couple.id, donor.id) == []

    store_explanation(
        db,
        couple.id,
        donor.id,
        [CitedSentence("First.", "donor.panel", "ACMG-SF", "record")],
    )
    store_explanation(
        db,
        couple.id,
        donor.id,
        [CitedSentence("Second.", "donor.panel", "ACMG-SF", "record")],
    )
    db.commit()

    assert latest_explanation(db, couple.id, donor.id)[0].text == "Second."


def test_delete_account_removes_only_that_accounts_records(db):
    couple = _user(db, "couple@example.com")
    other = _user(db, "other@example.com")
    bank = _user(db, "bank@example.com", role="bank")
    get_or_create_profile(db, couple.id)
    get_or_create_history(db, couple.id)
    get_or_create_survey(db, couple.id)
    get_or_create_profile(db, other.id)
    db.add(
        Carrier(
            subject_type="couple",
            subject_id=couple.id,
            gene="CFTR",
            zygosity="heterozygous",
        )
    )
    donor = _donor(db, bank.id, "DN-1")
    db.add(
        Carrier(
            subject_type="donor",
            subject_id=donor.id,
            gene="HBB",
            zygosity="heterozygous",
        )
    )
    store_explanation(
        db,
        couple.id,
        donor.id,
        [CitedSentence("Note.", "donor.panel", "ACMG-SF", "record")],
    )
    store_explanation(
        db,
        bank.id,
        donor.id,
        [CitedSentence("Bank.", "donor.panel", "ACMG-SF", "record")],
    )
    db.commit()
    couple_id = couple.id
    other_id = other.id
    bank_id = bank.id
    donor_id = donor.id

    delete_account(db, couple)
    db.commit()

    assert db.get(User, couple_id) is None
    assert db.get(CoupleProfile, couple_id) is None
    assert db.get(PriorHistory, couple_id) is None
    assert db.get(CoupleSurvey, couple_id) is None
    assert list_carriers(db, "couple", couple_id) == []
    assert db.get(LlmLog, 1) is None
    assert db.get(User, other_id) is not None
    assert db.get(CoupleProfile, other_id) is not None
    assert db.get(Donor, donor_id) is not None

    delete_account(db, db.get(User, bank_id))
    db.commit()

    assert db.get(User, bank_id) is None
    assert db.get(Donor, donor_id) is None
    assert list_carriers(db, "donor", donor_id) == []
    assert db.scalar(select(func.count()).select_from(LlmLog)) == 0
