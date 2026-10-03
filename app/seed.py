"""Synthetic demo accounts and donors. No real genotypes or photographs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
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
# * Precomputed sample clips under static/ so Vercel can play them without the motility service.
DEMO_MOTILITY_VIDEO_OK = "/static/motility/demo-tracked.mp4"
DEMO_MOTILITY_VIDEO_LOW = "/static/motility/demo-low-tracked.mp4"

DEMO_BLURBS = {
    "DN-100": (
        "Cleared, CMV negative, open ID, family limit 25, Rh negative, no shared carrier gene. "
        "Motility above WHO reference. For the sample couple this is the clean candidate."
    ),
    "DN-240": (
        "Heterozygous CFTR. If the couple also lists CFTR, this row is a hard stop and stays visible."
    ),
    "DN-310": (
        "CMV positive and anonymous, family limit 5, and motility below WHO reference. Soft weights "
        "when the couple needs CMV negative or open ID; motility adds a ranking penalty."
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


def _backfill_demo_motility(db: Session) -> None:
    """Fill motility readouts on demo donors when an older DB is missing them."""
    by_code = {
        donor.code: donor for donor in db.scalars(select(Donor)).all() if donor.code
    }
    donor_100 = by_code.get("DN-100")
    if donor_100 is not None and donor_100.motility_total_pct is None:
        donor_100.motility_total_pct = 69.0
        donor_100.motility_progressive_pct = 45.1
        donor_100.motility_video_url = DEMO_MOTILITY_VIDEO_OK
        donor_100.motility_below_reference = False
    donor_310 = by_code.get("DN-310")
    if donor_310 is not None and donor_310.motility_total_pct is None:
        donor_310.motility_total_pct = 28.0
        donor_310.motility_progressive_pct = 12.7
        donor_310.motility_video_url = DEMO_MOTILITY_VIDEO_LOW
        donor_310.motility_below_reference = True


def seed_demo(db: Session) -> None:
    """Insert the demo bank, donors, couple, counselor, and open slots.

    Idempotent: if the demo bank email already exists, this is a no-op so
    shared /tmp databases are not wiped or duplicated on cold start.
    Missing motility fields on seeded donors are backfilled so older DBs
    still show the Motility test section on Vercel.

    Args:
        db: Open session. The caller commits.
    """
    existing = db.scalars(select(User).where(User.email == DEMO_BANK_EMAIL)).first()
    if existing is not None:
        _backfill_demo_motility(db)
        return

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
            # * From samples/demo — above WHO total/progressive floors.
            motility_total_pct=69.0,
            motility_progressive_pct=45.1,
            motility_video_url=DEMO_MOTILITY_VIDEO_OK,
            motility_below_reference=False,
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
            # * From samples/demo_low_motility — flags ranking penalty.
            motility_total_pct=28.0,
            motility_progressive_pct=12.7,
            motility_video_url=DEMO_MOTILITY_VIDEO_LOW,
            motility_below_reference=True,
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
