"""Synthetic demo accounts and donors. No real genotypes or photographs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants import APPOINTMENT_BOOKED, DEFAULT_SLOT_MINUTES
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
from app.security import hash_password
from app.services import add_to_shortlist, appointment_donor_ids

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

# * Synthetic physical traits. DN-630 leaves height and weight blank on purpose.
DEMO_TRAITS = {
    "DN-100": {
        "hair_color": "blond",
        "hair_type": "straight",
        "eye_color": "blue",
        "height_cm": 185,
        "weight_kg": 80,
        "ethnicity": "Northern European",
        "baby_photo_key": "baby-1",
    },
    "DN-240": {
        "hair_color": "brown",
        "hair_type": "wavy",
        "eye_color": "green",
        "height_cm": 178,
        "weight_kg": 74,
        "ethnicity": "Northern European",
        "baby_photo_key": "baby-2",
    },
    "DN-310": {
        "hair_color": "black",
        "hair_type": "straight",
        "eye_color": "brown",
        "height_cm": 175,
        "weight_kg": 70,
        "ethnicity": "East Asian",
        "baby_photo_key": "baby-3",
    },
    "DN-410": {
        "hair_color": "brown",
        "hair_type": "curly",
        "eye_color": "hazel",
        "height_cm": 172,
        "weight_kg": 77,
        "ethnicity": "Ashkenazi Jewish",
        "baby_photo_key": "baby-4",
    },
    "DN-520": {
        "hair_color": "black",
        "hair_type": "wavy",
        "eye_color": "brown",
        "height_cm": 180,
        "weight_kg": 95,
        "ethnicity": "East Asian",
        "baby_photo_key": "baby-5",
    },
    "DN-630": {
        "hair_color": "red",
        "hair_type": "curly",
        "eye_color": "blue",
        "baby_photo_key": "baby-6",
    },
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


def _backfill_demo_consent(db: Session) -> None:
    """Grant consent on demo logins so cold starts do not re-open the gate."""
    now = datetime.now(timezone.utc)
    for email in (DEMO_BANK_EMAIL, DEMO_COUPLE_EMAIL, DEMO_COUNSELOR_EMAIL):
        user = db.scalars(select(User).where(User.email == email)).first()
        if user is not None and user.consent_at is None:
            user.consent_at = now


def _ensure_demo_couple_intake(db: Session, couple_id: int) -> None:
    """Fill the sample walkthrough intake so Matches and full profiles work.

    Matches stay empty until preferences_done. Vercel /tmp reseeds often, so
    the demo couple must ship ready for the ranked deck and DN-240 hard stop.
    """
    if db.get(CoupleProfile, couple_id) is None:
        db.add(
            CoupleProfile(
                user_id=couple_id,
                blood_type="O",
                rh="negative",
                adult_photo_key="",
            )
        )
    if db.get(PriorHistory, couple_id) is None:
        db.add(
            PriorHistory(
                user_id=couple_id,
                prior_pregnancies=1,
                prior_donors="",
                miscarriages=0,
                known_conditions="",
            )
        )
    survey = db.get(CoupleSurvey, couple_id)
    if survey is None:
        db.add(
            CoupleSurvey(
                user_id=couple_id,
                ancestry="Finnish",
                cmv_requirement="any",
                clinical_notes="",
                id_release="open",
                family_limit=2,
                clinical_done=True,
                preferences_done=True,
            )
        )
    elif not survey.preferences_done or survey.family_limit is None:
        # * Older demo DBs that only created the login still need a deck.
        survey.ancestry = survey.ancestry or "Finnish"
        survey.cmv_requirement = survey.cmv_requirement or "any"
        survey.id_release = survey.id_release or "open"
        survey.family_limit = survey.family_limit or 2
        survey.clinical_done = True
        survey.preferences_done = True
    has_cftr = db.scalars(
        select(Carrier).where(
            Carrier.subject_type == "couple",
            Carrier.subject_id == couple_id,
            Carrier.gene == "CFTR",
        )
    ).first()
    if has_cftr is None:
        db.add(
            Carrier(
                subject_type="couple",
                subject_id=couple_id,
                gene="CFTR",
                zygosity="heterozygous",
                condition="Cystic fibrosis",
            )
        )


def _ensure_demo_visit(db: Session, couple: User, counselor: User) -> None:
    """Shortlist DN-100/DN-240 and book a demo visit for the counselor walkthrough.

    Never recreates a visit after cancel. Cold starts call this again on Vercel;
    re-booking here made cancel look broken.
    """
    donors = {
        donor.code: donor
        for donor in db.scalars(
            select(Donor).where(Donor.code.in_(("DN-100", "DN-240")))
        )
    }
    dn100 = donors.get("DN-100")
    dn240 = donors.get("DN-240")
    if dn100 is None or dn240 is None:
        return
    for donor in (dn100, dn240):
        add_to_shortlist(db, couple.id, donor.id)
    existing = db.scalars(
        select(Appointment)
        .where(
            Appointment.couple_user_id == couple.id,
            Appointment.counselor_user_id == counselor.id,
        )
        .order_by(Appointment.id)
    ).first()
    if existing is not None:
        if existing.status != APPOINTMENT_BOOKED:
            return
        booked = existing
    else:
        slot = db.scalars(
            select(AvailabilitySlot)
            .where(AvailabilitySlot.counselor_user_id == counselor.id)
            .order_by(AvailabilitySlot.starts_at)
        ).first()
        if slot is None:
            return
        booked = Appointment(
            couple_user_id=couple.id,
            counselor_user_id=counselor.id,
            slot_id=slot.id,
            status=APPOINTMENT_BOOKED,
            created_at=datetime.now(timezone.utc),
        )
        db.add(booked)
        db.flush()
    existing_ids = set(appointment_donor_ids(db, booked.id))
    for donor in (dn100, dn240):
        if donor.id in existing_ids:
            continue
        db.add(
            AppointmentDonor(
                appointment_id=booked.id,
                donor_id=donor.id,
                donor_code=donor.code,
            )
        )


def seed_demo(db: Session) -> None:
    """Insert the demo bank, donors, couple, counselor, and open slots.

    Idempotent: if the demo bank email already exists, this is a no-op so
    shared /tmp databases are not wiped or duplicated on cold start.
    Missing motility fields on seeded donors are backfilled so older DBs
    still show the Motility test section on Vercel. Demo couple intake is
    also backfilled so Matches is not empty after a /tmp recycle.

    Args:
        db: Open session. The caller commits.
    """
    existing = db.scalars(select(User).where(User.email == DEMO_BANK_EMAIL)).first()
    if existing is not None:
        _backfill_demo_motility(db)
        _backfill_demo_consent(db)
        couple = db.scalars(select(User).where(User.email == DEMO_COUPLE_EMAIL)).first()
        counselor = db.scalars(
            select(User).where(User.email == DEMO_COUNSELOR_EMAIL)
        ).first()
        if couple is not None:
            _ensure_demo_couple_intake(db, couple.id)
            if counselor is not None:
                _ensure_demo_visit(db, couple, counselor)
        return

    # * Pre-consent demo logins so Vercel /tmp reseeds do not re-open the gate.
    consented = datetime.now(timezone.utc)
    bank = User(
        email=DEMO_BANK_EMAIL,
        password_hash=hash_password(DEMO_BANK_PASSWORD),
        role="bank",
        consent_at=consented,
    )
    couple = User(
        email=DEMO_COUPLE_EMAIL,
        password_hash=hash_password(DEMO_COUPLE_PASSWORD),
        role="couple",
        paid_at=datetime.now(timezone.utc),
        consent_at=consented,
    )
    counselor = User(
        email=DEMO_COUNSELOR_EMAIL,
        password_hash=hash_password(DEMO_COUNSELOR_PASSWORD),
        role="counselor",
        consent_at=consented,
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
    _ensure_demo_couple_intake(db, couple.id)
    _ensure_demo_visit(db, couple, counselor)
