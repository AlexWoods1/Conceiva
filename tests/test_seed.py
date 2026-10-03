"""Tests for the synthetic demo seed."""

from sqlalchemy import select

from app.models import Carrier, Donor, User
from app.security import verify_password
from app.seed import (
    DEMO_BANK_EMAIL,
    DEMO_BANK_PASSWORD,
    DEMO_COUPLE_EMAIL,
    DEMO_COUPLE_PASSWORD,
    seed_demo,
)


def test_seed_demo_inserts_logins_donors_and_carrier_rows(db):
    seed_demo(db)
    db.commit()

    couple = db.scalars(select(User).where(User.email == DEMO_COUPLE_EMAIL)).one()
    bank = db.scalars(select(User).where(User.email == DEMO_BANK_EMAIL)).one()
    donors = list(db.scalars(select(Donor).order_by(Donor.code)))
    carriers = list(db.scalars(select(Carrier)))

    assert couple.role == "couple"
    assert couple.consent_at is None
    assert verify_password(DEMO_COUPLE_PASSWORD, couple.password_hash)
    assert bank.role == "bank"
    assert verify_password(DEMO_BANK_PASSWORD, bank.password_hash)
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
