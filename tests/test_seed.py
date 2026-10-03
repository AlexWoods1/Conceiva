"""Tests for the synthetic demo seed."""

from sqlalchemy import select

from app.models import AvailabilitySlot, Carrier, CounselorProfile, Donor, User
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
    assert {(row.gene, row.subject_id) for row in carriers} == {
        ("CFTR", donors[1].id),
        ("HBB", donors[3].id),
    }


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
