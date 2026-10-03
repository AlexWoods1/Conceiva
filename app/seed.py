"""Synthetic demo accounts and donors. No real genotypes or photographs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.constants import DEFAULT_SLOT_MINUTES
from app.models import AvailabilitySlot, Carrier, CounselorProfile, Donor, User
from app.security import hash_password

DEMO_COUPLE_EMAIL = "couple@demo.local"
DEMO_COUPLE_PASSWORD = "demo-couple"
DEMO_BANK_EMAIL = "bank@demo.local"
DEMO_BANK_PASSWORD = "demo-bank"
DEMO_COUNSELOR_EMAIL = "counselor@demo.local"
DEMO_COUNSELOR_PASSWORD = "demo-counselor"
DEMO_COUNSELOR_NAME = "Demo Genetic Counselor"

# * Shown on match cards so the seeded rank can be read without opening each row.
DEMO_BLURBS = {
    "DN-100": (
        "Cleared, CMV negative, open ID, family limit 25, Rh negative, no shared carrier gene. "
        "For the sample couple this is the clean candidate."
    ),
    "DN-240": (
        "Heterozygous CFTR. If the couple also lists CFTR, this row is a hard stop and stays visible."
    ),
    "DN-310": (
        "CMV positive and anonymous, family limit 5. Those are soft weights when the couple needs "
        "CMV negative or open ID."
    ),
    "DN-410": (
        "Still in quarantine, family limit 1, anonymous, heterozygous HBB. Quarantine and the "
        "survey mismatches lower the score. HBB blocks only a couple who also carries HBB."
    ),
    "DN-520": (
        "Medically clear, but anonymous with a family limit of 1. A couple who wants open ID and "
        "more than one family slot sees a lower rank, not a block."
    ),
    "DN-630": (
        "Rh positive, panel left blank, CMV not recorded. The Rh line is a flag. The explanation "
        "should say the panel is not in the record."
    ),
}

# * Synthetic physical traits. DN-630 leaves height and weight blank on purpose.
DEMO_TRAITS = {
    "DN-100": {
        "hair_color": "blond",
        "hair_type": "straight",
        "eye_color": "blue",
        "height_cm": 185,
        "weight_kg": 80,
        "ethnicity": "Northern European",
    },
    "DN-240": {
        "hair_color": "brown",
        "hair_type": "wavy",
        "eye_color": "green",
        "height_cm": 178,
        "weight_kg": 74,
        "ethnicity": "Northern European",
    },
    "DN-310": {
        "hair_color": "black",
        "hair_type": "straight",
        "eye_color": "brown",
        "height_cm": 175,
        "weight_kg": 70,
        "ethnicity": "East Asian",
    },
    "DN-410": {
        "hair_color": "brown",
        "hair_type": "curly",
        "eye_color": "hazel",
        "height_cm": 172,
        "weight_kg": 77,
        "ethnicity": "Ashkenazi Jewish",
    },
    "DN-520": {
        "hair_color": "black",
        "hair_type": "wavy",
        "eye_color": "brown",
        "height_cm": 180,
        "weight_kg": 95,
        "ethnicity": "East Asian",
    },
    "DN-630": {"hair_color": "red", "hair_type": "curly", "eye_color": "blue"},
}


def seed_demo(db: Session) -> None:
    """Insert the demo bank, donors, couple, counselor, and open slots.

    Args:
        db: Open session. The caller commits.
    """
    bank = User(
        email=DEMO_BANK_EMAIL,
        password_hash=hash_password(DEMO_BANK_PASSWORD),
        role="bank",
    )
    couple = User(
        email=DEMO_COUPLE_EMAIL,
        password_hash=hash_password(DEMO_COUPLE_PASSWORD),
        role="couple",
        paid_at=datetime.now(timezone.utc),
    )
    counselor = User(
        email=DEMO_COUNSELOR_EMAIL,
        password_hash=hash_password(DEMO_COUNSELOR_PASSWORD),
        role="counselor",
    )
    db.add_all([bank, couple, counselor])
    db.flush()
    db.add(CounselorProfile(user_id=counselor.id, display_name=DEMO_COUNSELOR_NAME))

    # * Fixed offsets from "now" so the book page always has open future slots.
    # * Store naive UTC; SQLite does not keep tzinfo reliably.
    now = datetime.now(timezone.utc).replace(
        minute=0, second=0, microsecond=0, tzinfo=None
    )
    for days in (1, 2, 3, 5, 7):
        db.add(
            AvailabilitySlot(
                counselor_user_id=counselor.id,
                starts_at=now + timedelta(days=days, hours=15),
                duration_minutes=DEFAULT_SLOT_MINUTES,
            )
        )

    donors = [
        Donor(
            bank_user_id=bank.id,
            code="DN-100",
            blood_type="O",
            rh="negative",
            ancestry="Finnish",
            photo_key="geo-1",
            panel="ACMG-SF",
            cmv="negative",
            quarantine="cleared",
            family_limit=25,
            id_release_policy="open",
            catalog_confirmed=True,
        ),
        Donor(
            bank_user_id=bank.id,
            code="DN-240",
            blood_type="A",
            rh="positive",
            ancestry="Finnish",
            photo_key="geo-2",
            panel="ACMG-SF",
            cmv="negative",
            quarantine="cleared",
            family_limit=10,
            id_release_policy="open",
            catalog_confirmed=True,
        ),
        Donor(
            bank_user_id=bank.id,
            code="DN-310",
            blood_type="B",
            rh="positive",
            ancestry="Korean",
            photo_key="geo-3",
            panel="ACMG-SF",
            cmv="positive",
            quarantine="cleared",
            family_limit=5,
            id_release_policy="anonymous",
            catalog_confirmed=True,
        ),
        Donor(
            bank_user_id=bank.id,
            code="DN-410",
            blood_type="AB",
            rh="negative",
            ancestry="Ashkenazi Jewish",
            photo_key="geo-4",
            panel="ACMG-SF",
            cmv="negative",
            quarantine="in_quarantine",
            family_limit=1,
            id_release_policy="anonymous",
            catalog_confirmed=True,
        ),
        Donor(
            bank_user_id=bank.id,
            code="DN-520",
            blood_type="O",
            rh="negative",
            ancestry="Japanese",
            photo_key="geo-5",
            panel="ACMG-SF",
            cmv="negative",
            quarantine="cleared",
            family_limit=1,
            id_release_policy="anonymous",
            catalog_confirmed=True,
        ),
        Donor(
            bank_user_id=bank.id,
            code="DN-630",
            blood_type="A",
            rh="positive",
            ancestry="Finnish",
            photo_key="geo-6",
            panel="",
            cmv="unknown",
            quarantine="cleared",
            family_limit=10,
            id_release_policy="open",
            catalog_confirmed=True,
        ),
    ]
    for donor in donors:
        for name, value in DEMO_TRAITS.get(donor.code, {}).items():
            setattr(donor, name, value)
    db.add_all(donors)
    db.flush()
    db.add(
        Carrier(
            subject_type="donor",
            subject_id=donors[1].id,
            gene="CFTR",
            zygosity="heterozygous",
            condition="Cystic fibrosis",
        )
    )
    db.add(
        Carrier(
            subject_type="donor",
            subject_id=donors[3].id,
            gene="HBB",
            zygosity="heterozygous",
            condition="Sickle cell disease",
        )
    )
