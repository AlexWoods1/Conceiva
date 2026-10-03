"""Tests for account, survey, and match persistence."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.config import Settings
from app.constants import APPOINTMENT_BOOKED, APPOINTMENT_CANCELLED, MOTILITY_PENALTY
from app.llm import CitedSentence
from app.matching import confirmed_genes
from app.models import (
    Appointment,
    AvailabilitySlot,
    CandidateReport,
    Carrier,
    CoupleProfile,
    CoupleSurvey,
    Donor,
    LlmLog,
    PriorHistory,
    ShortlistItem,
    User,
)
from app.phenotype import resemblance_score
from app.security import hash_password
from app.services import (
    add_to_shortlist,
    appointment_donor_ids,
    book_appointment,
    cancel_appointment,
    counselor_display_name,
    delete_account,
    delete_donor,
    find_ranked_donor,
    get_or_create_counselor_profile,
    get_or_create_history,
    get_or_create_profile,
    get_or_create_survey,
    home_path_for,
    latest_candidate_report,
    latest_explanation,
    list_carriers,
    list_shortlist,
    open_future_slots,
    packet_for,
    ranked_matches,
    score_donor_for_couple,
    sentences_from_json,
    sentences_to_json,
    slot_is_open,
    store_candidate_report,
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


def test_delete_donor_clears_shortlist_and_carrier_rows(db):
    couple = _user(db, "couple@example.com")
    bank = _user(db, "bank@example.com", role="bank")
    donor = _donor(db, bank.id, "DN-1")
    other = _donor(db, bank.id, "DN-2")
    db.add(
        Carrier(
            subject_type="donor",
            subject_id=donor.id,
            gene="CFTR",
            zygosity="heterozygous",
        )
    )
    db.commit()
    assert add_to_shortlist(db, couple.id, donor.id) is None
    assert add_to_shortlist(db, couple.id, other.id) is None
    db.commit()

    delete_donor(db, donor)
    db.commit()

    assert db.get(Donor, donor.id) is None
    assert db.get(Donor, other.id) is not None
    assert list_shortlist(db, couple.id)[0].donor_id == other.id
    assert list_carriers(db, "donor", donor.id) == []
    assert db.scalar(select(func.count()).select_from(ShortlistItem)) == 1


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


def test_home_path_for_roles():
    assert home_path_for(User(email="a", password_hash="x", role="bank")) == "/bank"
    assert (
        home_path_for(User(email="b", password_hash="x", role="counselor"))
        == "/counselor"
    )
    assert (
        home_path_for(User(email="c", password_hash="x", role="couple"))
        == "/couple/history"
    )


def test_sentences_json_round_trip():
    original = [
        CitedSentence("Panel listed.", "donor.panel", "ACMG-SF", "record"),
        CitedSentence("Hard stop.", "carriers.gene", "CFTR", "hard_stop"),
    ]

    restored = sentences_from_json(sentences_to_json(original))

    assert [(item.text, item.field, item.value, item.kind) for item in restored] == [
        (item.text, item.field, item.value, item.kind) for item in original
    ]


def test_add_to_shortlist_rules(db, monkeypatch):
    couple = _user(db, "couple@example.com")
    bank = _user(db, "bank@example.com", role="bank")
    draft = _donor(db, bank.id, "DRAFT", catalog_confirmed=False)
    first = _donor(db, bank.id, "DN-1")
    second = _donor(db, bank.id, "DN-2")

    assert add_to_shortlist(db, couple.id, draft.id) == (
        "That donor is not on the confirmed inventory."
    )
    assert add_to_shortlist(db, couple.id, first.id) is None
    db.commit()
    assert add_to_shortlist(db, couple.id, first.id) is None
    assert len(list_shortlist(db, couple.id)) == 1

    monkeypatch.setattr("app.services.MAX_SHORTLIST", 1)
    assert "limited to" in add_to_shortlist(db, couple.id, second.id)


def test_slot_is_open_and_open_future_slots_respect_clock(db):
    counselor = _user(db, "counselor@example.com", role="counselor")
    couple = _user(db, "couple@example.com")
    now = datetime(2030, 1, 15, 12, 0, tzinfo=timezone.utc)
    past = AvailabilitySlot(
        counselor_user_id=counselor.id,
        starts_at=now - timedelta(hours=1),
        duration_minutes=45,
    )
    future = AvailabilitySlot(
        counselor_user_id=counselor.id,
        starts_at=now + timedelta(hours=2),
        duration_minutes=45,
    )
    db.add_all([past, future])
    db.flush()
    db.add(
        Appointment(
            couple_user_id=couple.id,
            counselor_user_id=counselor.id,
            slot_id=future.id,
            status=APPOINTMENT_BOOKED,
            created_at=now,
        )
    )
    db.commit()

    assert slot_is_open(db, past, now=now) is False
    assert slot_is_open(db, future, now=now) is False
    assert open_future_slots(db, now=now) == []

    cancel_appointment(db, db.scalars(select(Appointment)).one())
    db.commit()
    assert slot_is_open(db, future, now=now) is True
    assert [slot.id for slot in open_future_slots(db, now=now)] == [future.id]


def test_book_and_cancel_appointment(db):
    couple = _user(db, "couple@example.com")
    counselor = _user(db, "counselor@example.com", role="counselor")
    bank = _user(db, "bank@example.com", role="bank")
    donor = _donor(db, bank.id, "DN-1")
    now = datetime(2030, 2, 1, 10, 0, tzinfo=timezone.utc)
    slot = AvailabilitySlot(
        counselor_user_id=counselor.id,
        starts_at=now + timedelta(days=1),
        duration_minutes=45,
    )
    db.add(slot)
    db.commit()

    empty = book_appointment(db, couple, slot, now=now)
    assert isinstance(empty, str)
    assert "shortlist" in empty

    assert add_to_shortlist(db, couple.id, donor.id) is None
    db.commit()
    appointment = book_appointment(db, couple, slot, now=now)
    db.commit()
    assert isinstance(appointment, Appointment)
    assert appointment.status == APPOINTMENT_BOOKED
    assert appointment_donor_ids(db, appointment.id) == [donor.id]

    assert cancel_appointment(db, appointment) is None
    db.commit()
    assert appointment.status == APPOINTMENT_CANCELLED
    assert cancel_appointment(db, appointment) == "That visit is already cancelled."


def test_store_and_latest_candidate_report_upserts(db):
    couple = _user(db, "couple@example.com")
    counselor = _user(db, "counselor@example.com", role="counselor")
    bank = _user(db, "bank@example.com", role="bank")
    donor = _donor(db, bank.id, "DN-1")
    now = datetime(2030, 3, 1, tzinfo=timezone.utc)
    slot = AvailabilitySlot(
        counselor_user_id=counselor.id, starts_at=now, duration_minutes=45
    )
    db.add(slot)
    db.flush()
    appointment = Appointment(
        couple_user_id=couple.id,
        counselor_user_id=counselor.id,
        slot_id=slot.id,
        status=APPOINTMENT_BOOKED,
        created_at=now,
    )
    db.add(appointment)
    db.commit()

    store_candidate_report(
        db,
        appointment.id,
        donor.id,
        [CitedSentence("First.", "donor.panel", "ACMG-SF", "record")],
    )
    store_candidate_report(
        db,
        appointment.id,
        donor.id,
        [CitedSentence("Second.", "donor.panel", "ACMG-SF", "record")],
    )
    db.commit()

    rows = list(db.scalars(select(CandidateReport)))
    assert len(rows) == 1
    assert latest_candidate_report(db, appointment.id, donor.id)[0].text == "Second."


def test_counselor_display_name_falls_back_to_email(db):
    counselor = _user(db, "counselor@example.com", role="counselor")
    assert counselor_display_name(db, counselor) == "counselor@example.com"

    profile = get_or_create_counselor_profile(db, counselor.id)
    profile.display_name = "Ada Counselor"
    db.commit()
    assert counselor_display_name(db, counselor) == "Ada Counselor"


def test_delete_account_counselor_removes_slots_and_visits(db):
    couple = _user(db, "couple@example.com")
    counselor = _user(db, "counselor@example.com", role="counselor")
    bank = _user(db, "bank@example.com", role="bank")
    donor = _donor(db, bank.id, "DN-1")
    get_or_create_counselor_profile(db, counselor.id)
    now = datetime(2030, 4, 1, tzinfo=timezone.utc)
    slot = AvailabilitySlot(
        counselor_user_id=counselor.id, starts_at=now, duration_minutes=45
    )
    db.add(slot)
    db.flush()
    appointment = Appointment(
        couple_user_id=couple.id,
        counselor_user_id=counselor.id,
        slot_id=slot.id,
        status=APPOINTMENT_BOOKED,
        created_at=now,
    )
    db.add(appointment)
    db.flush()
    store_candidate_report(
        db,
        appointment.id,
        donor.id,
        [CitedSentence("Note.", "donor.panel", "ACMG-SF", "record")],
    )
    db.commit()
    counselor_id = counselor.id
    slot_id = slot.id
    appointment_id = appointment.id

    delete_account(db, counselor)
    db.commit()

    assert db.get(User, counselor_id) is None
    assert db.get(AvailabilitySlot, slot_id) is None
    assert db.get(Appointment, appointment_id) is None
    assert db.scalar(select(func.count()).select_from(CandidateReport)) == 0
    assert db.get(User, couple.id) is not None


def test_score_donor_for_couple_passes_motility_flag(db):
    couple = _user(db, "couple@example.com")
    bank = _user(db, "bank@example.com", role="bank")
    donor = _donor(db, bank.id, "DN-1", motility_below_reference=True)
    _ready_survey(db, couple.id)

    result = score_donor_for_couple(db, couple, donor, _settings())

    assert result.score == 100 - MOTILITY_PENALTY
    assert result.hard_stop is False
    assert any(
        reason.field == "donor.motility_below_reference" for reason in result.reasons
    )
