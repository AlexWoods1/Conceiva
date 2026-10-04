"""Tests for the synthetic demo seed."""

from sqlalchemy import select

from app.models import (
    Appointment,
    AppointmentDonor,
    AvailabilitySlot,
    Carrier,
    CounselorProfile,
    CoupleProfile,
    CoupleSurvey,
    Donor,
    PriorHistory,
    User,
)
from app.security import verify_password
from app.seed import (
    DEMO_BANK_EMAIL,
    DEMO_BANK_PASSWORD,
    DEMO_COUNSELOR_EMAIL,
    DEMO_COUNSELOR_NAME,
    DEMO_COUNSELOR_PASSWORD,
    DEMO_COUPLE_EMAIL,
    DEMO_COUPLE_PASSWORD,
    seed_demo,
)


def test_seed_demo_inserts_logins_donors_and_carrier_rows(db):
    seed_demo(db)
    db.commit()

    couple = db.scalars(select(User).where(User.email == DEMO_COUPLE_EMAIL)).one()
    bank = db.scalars(select(User).where(User.email == DEMO_BANK_EMAIL)).one()
    counselor = db.scalars(select(User).where(User.email == DEMO_COUNSELOR_EMAIL)).one()
    donors = list(db.scalars(select(Donor).order_by(Donor.code)))
    carriers = list(db.scalars(select(Carrier)))

    assert couple.role == "couple"
    assert couple.consent_at is not None
    assert verify_password(DEMO_COUPLE_PASSWORD, couple.password_hash)
    assert bank.role == "bank"
    assert bank.consent_at is not None
    assert verify_password(DEMO_BANK_PASSWORD, bank.password_hash)
    assert counselor.role == "counselor"
    assert counselor.consent_at is not None
    assert verify_password(DEMO_COUNSELOR_PASSWORD, counselor.password_hash)
    assert [donor.code for donor in donors] == [
        "DN-100",
        "DN-240",
        "DN-310",
        "DN-410",
        "DN-520",
        "DN-630",
    ]
    assert all(
        donor.bank_user_id == bank.id and donor.catalog_confirmed for donor in donors
    )
    assert {(row.gene, row.subject_type, row.subject_id) for row in carriers} == {
        ("CFTR", "donor", donors[1].id),
        ("HBB", "donor", donors[3].id),
        ("CFTR", "couple", couple.id),
    }


def test_seed_demo_prepares_couple_intake_for_matches(db):
    seed_demo(db)
    db.commit()

    couple = db.scalars(select(User).where(User.email == DEMO_COUPLE_EMAIL)).one()
    profile = db.get(CoupleProfile, couple.id)
    history = db.get(PriorHistory, couple.id)
    survey = db.get(CoupleSurvey, couple.id)

    assert profile is not None
    assert profile.blood_type == "O"
    assert profile.rh == "negative"
    assert history is not None
    assert history.prior_pregnancies == 1
    assert survey is not None
    assert survey.preferences_done is True
    assert survey.family_limit == 2
    assert survey.id_release == "open"
    assert survey.ancestry == "Finnish"


def test_seed_demo_backfills_couple_intake_on_existing_demo(db):
    seed_demo(db)
    db.commit()
    couple = db.scalars(select(User).where(User.email == DEMO_COUPLE_EMAIL)).one()
    survey = db.get(CoupleSurvey, couple.id)
    assert survey is not None
    survey.preferences_done = False
    survey.family_limit = None
    db.commit()

    seed_demo(db)
    db.commit()
    db.refresh(survey)

    assert survey.preferences_done is True
    assert survey.family_limit == 2


def test_seed_demo_books_counselor_visit_with_matchable_candidates(db):
    from pathlib import Path

    from app.config import Settings
    from app.services import resolve_visit_donors, score_donor_for_couple

    seed_demo(db)
    db.commit()

    couple = db.scalars(select(User).where(User.email == DEMO_COUPLE_EMAIL)).one()
    counselor = db.scalars(select(User).where(User.email == DEMO_COUNSELOR_EMAIL)).one()
    appointment = db.scalars(
        select(Appointment).where(
            Appointment.couple_user_id == couple.id,
            Appointment.counselor_user_id == counselor.id,
        )
    ).one()
    donors = resolve_visit_donors(db, appointment.id)
    codes = {donor.code for donor in donors}
    assert codes == {"DN-100", "DN-240"}
    assert all(
        row.donor_code in {"DN-100", "DN-240"}
        for row in db.scalars(
            select(AppointmentDonor).where(
                AppointmentDonor.appointment_id == appointment.id
            )
        )
    )

    settings = Settings(
        database_path=Path("unused.db"),
        session_secret="test",
        llm_api_key="",
        llm_base_url="https://llm.example/v1",
        llm_model="test",
        enable_face_compare=False,
        seed_on_empty=False,
    )
    by_code = {
        donor.code: score_donor_for_couple(db, couple, donor, settings)
        for donor in donors
    }
    assert by_code["DN-240"].hard_stop is True
    assert by_code["DN-100"].hard_stop is False


def test_seed_demo_does_not_rebook_after_cancel(db):
    from app.services import cancel_appointment

    seed_demo(db)
    db.commit()
    appointment = db.scalars(select(Appointment)).one()
    assert cancel_appointment(db, appointment) is None
    db.commit()

    seed_demo(db)
    db.commit()
    db.refresh(appointment)

    assert appointment.status == "cancelled"
    assert list(db.scalars(select(Appointment))) == [appointment]


def test_seed_demo_marks_couple_paid_and_creates_future_slots(db):
    seed_demo(db)
    db.commit()

    couple = db.scalars(select(User).where(User.email == DEMO_COUPLE_EMAIL)).one()
    counselor = db.scalars(select(User).where(User.email == DEMO_COUNSELOR_EMAIL)).one()
    profile = db.get(CounselorProfile, counselor.id)
    slots = list(
        db.scalars(
            select(AvailabilitySlot).where(
                AvailabilitySlot.counselor_user_id == counselor.id
            )
        )
    )
    donor_630 = db.scalars(select(Donor).where(Donor.code == "DN-630")).one()

    assert couple.paid_at is not None
    assert profile is not None
    assert profile.display_name == DEMO_COUNSELOR_NAME
    assert len(slots) >= 3
    assert donor_630.panel == ""
    assert donor_630.cmv == "unknown"


def test_seed_demo_includes_motility_readouts_for_demo_donors(db):
    from app.seed import DEMO_MOTILITY_VIDEO_LOW, DEMO_MOTILITY_VIDEO_OK

    seed_demo(db)
    db.commit()

    donor_100 = db.scalars(select(Donor).where(Donor.code == "DN-100")).one()
    donor_310 = db.scalars(select(Donor).where(Donor.code == "DN-310")).one()

    assert donor_100.motility_total_pct == 69.0
    assert donor_100.motility_progressive_pct == 45.1
    assert donor_100.motility_video_url == DEMO_MOTILITY_VIDEO_OK
    assert donor_100.motility_below_reference is False

    assert donor_310.motility_total_pct == 28.0
    assert donor_310.motility_progressive_pct == 12.7
    assert donor_310.motility_video_url == DEMO_MOTILITY_VIDEO_LOW
    assert donor_310.motility_below_reference is True


def test_seed_demo_backfills_motility_on_existing_demo_donors(db):
    from app.seed import DEMO_MOTILITY_VIDEO_OK

    seed_demo(db)
    db.commit()
    donor_100 = db.scalars(select(Donor).where(Donor.code == "DN-100")).one()
    donor_100.motility_total_pct = None
    donor_100.motility_progressive_pct = None
    donor_100.motility_video_url = ""
    donor_100.motility_below_reference = False
    db.commit()

    seed_demo(db)
    db.commit()
    db.refresh(donor_100)

    assert donor_100.motility_total_pct == 69.0
    assert donor_100.motility_video_url == DEMO_MOTILITY_VIDEO_OK


def test_seed_demo_is_idempotent(db):
    seed_demo(db)
    db.commit()
    seed_demo(db)
    db.commit()

    users = list(db.scalars(select(User)))
    donors = list(db.scalars(select(Donor)))
    assert len(users) == 3
    assert len(donors) == 6
