"""Database operations shared by the site."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.constants import APPOINTMENT_BOOKED, APPOINTMENT_CANCELLED, MAX_SHORTLIST
from app.counselor_report import build_counselor_packet, generate_counselor_report
from app.llm import CitedSentence, ExplainPacket, build_explain_packet
from app.matching import (
    DonorOffer,
    MatchResult,
    PersonGenetics,
    Preference,
    confirmed_genes,
    score_match,
)
from app.models import (
    Appointment,
    AppointmentDonor,
    AvailabilitySlot,
    CandidateReport,
    Carrier,
    CounselorProfile,
    CoupleProfile,
    CoupleSurvey,
    Donor,
    LlmLog,
    MotilitySample,
    PriorHistory,
    ShortlistItem,
    User,
)
from app.phenotype import resemblance_score


def get_or_create_profile(db: Session, user_id: int) -> CoupleProfile:
    """Return the couple profile, creating an empty one when needed."""
    row = db.get(CoupleProfile, user_id)
    if row is None:
        row = CoupleProfile(user_id=user_id, blood_type="", rh="", adult_photo_key="")
        db.add(row)
        db.flush()
    return row


def get_or_create_history(db: Session, user_id: int) -> PriorHistory:
    """Return prior history, creating an empty row when needed."""
    row = db.get(PriorHistory, user_id)
    if row is None:
        row = PriorHistory(
            user_id=user_id,
            prior_pregnancies=0,
            prior_donors="",
            miscarriages=0,
            known_conditions="",
        )
        db.add(row)
        db.flush()
    return row


def get_or_create_survey(db: Session, user_id: int) -> CoupleSurvey:
    """Return the couple survey, creating an empty row when needed."""
    row = db.get(CoupleSurvey, user_id)
    if row is None:
        row = CoupleSurvey(
            user_id=user_id,
            ancestry="",
            cmv_requirement="any",
            clinical_notes="",
            id_release="",
            family_limit=None,
            clinical_done=False,
            preferences_done=False,
        )
        db.add(row)
        db.flush()
    return row


def get_or_create_counselor_profile(db: Session, user_id: int) -> CounselorProfile:
    """Return the counselor profile, creating an empty one when needed."""
    row = db.get(CounselorProfile, user_id)
    if row is None:
        row = CounselorProfile(user_id=user_id, display_name="")
        db.add(row)
        db.flush()
    return row


def list_carriers(db: Session, subject_type: str, subject_id: int) -> list[Carrier]:
    """List carrier rows for a couple or a donor."""
    return list(
        db.scalars(
            select(Carrier)
            .where(
                Carrier.subject_type == subject_type, Carrier.subject_id == subject_id
            )
            .order_by(Carrier.gene, Carrier.id)
        )
    )


def score_donor_for_couple(
    db: Session, user: User, donor: Donor, settings: Settings
) -> MatchResult:
    """Score one live donor against one couple.

    Args:
        db: Open session.
        user: Couple user.
        donor: Donor row.
        settings: Process settings.

    Returns:
        Match result for the pair.
    """
    survey = get_or_create_survey(db, user.id)
    profile = get_or_create_profile(db, user.id)
    couple_rows = [
        (row.gene, row.zygosity) for row in list_carriers(db, "couple", user.id)
    ]
    person = PersonGenetics(
        blood_type=profile.blood_type,
        rh=profile.rh,
        ancestry=survey.ancestry,
        genes=confirmed_genes(couple_rows),
    )
    prefs = Preference(
        id_release=survey.id_release or "either",
        family_limit=survey.family_limit or 1,
        cmv_requirement=survey.cmv_requirement or "any",
    )
    donor_rows = [
        (row.gene, row.zygosity) for row in list_carriers(db, "donor", donor.id)
    ]
    face = resemblance_score(
        donor.photo_key, profile.adult_photo_key, settings.enable_face_compare
    )
    offer = DonorOffer(
        code=donor.code,
        blood_type=donor.blood_type,
        rh=donor.rh,
        ancestry=donor.ancestry,
        genes=confirmed_genes(donor_rows),
        panel=donor.panel,
        cmv=donor.cmv,
        quarantine=donor.quarantine,
        family_limit=donor.family_limit,
        id_release_policy=donor.id_release_policy,
        motility_below_reference=donor.motility_below_reference,
    )
    return score_match(person, prefs, offer, face_score=face)


def ranked_matches(
    db: Session, user: User, settings: Settings
) -> list[tuple[Donor, MatchResult]]:
    """Rank confirmed donors for a couple.

    Args:
        db: Open session.
        user: Couple user.
        settings: Process settings. The face flag is read from here.

    Returns:
        Donor and result pairs. Hard stops sort after clear matches.
        An empty list means preferences are not saved yet.
    """
    survey = db.get(CoupleSurvey, user.id)
    if survey is None or not survey.preferences_done or survey.family_limit is None:
        return []
    donors = list(db.scalars(select(Donor).where(Donor.catalog_confirmed.is_(True))))
    scored: list[tuple[Donor, MatchResult]] = []
    for donor in donors:
        scored.append((donor, score_donor_for_couple(db, user, donor, settings)))
    scored.sort(
        key=lambda item: (item[1].hard_stop, -item[1].score, item[0].code, item[0].id)
    )
    return scored


def packet_for(
    db: Session, user: User, donor: Donor, result: MatchResult
) -> ExplainPacket:
    """Build the explanation packet for one ranked donor."""
    profile = get_or_create_profile(db, user.id)
    survey = get_or_create_survey(db, user.id)
    history = db.get(PriorHistory, user.id)
    couple_rows = [
        (row.gene, row.zygosity) for row in list_carriers(db, "couple", user.id)
    ]
    person = PersonGenetics(
        blood_type=profile.blood_type,
        rh=profile.rh,
        ancestry=survey.ancestry,
        genes=confirmed_genes(couple_rows),
    )
    prefs = Preference(
        id_release=survey.id_release or "either",
        family_limit=survey.family_limit or 1,
        cmv_requirement=survey.cmv_requirement or "any",
    )
    if history is None:
        pregnancies = None
        miscarriages = None
        prior_donors = ""
        known = ""
    else:
        pregnancies = history.prior_pregnancies
        miscarriages = history.miscarriages
        prior_donors = history.prior_donors
        known = history.known_conditions
    return build_explain_packet(
        couple=person,
        prefs=prefs,
        result=result,
        donor_panel=donor.panel,
        known_conditions=known,
        prior_pregnancies=pregnancies,
        prior_donors=prior_donors,
        miscarriages=miscarriages,
    )


def store_explanation(
    db: Session,
    user_id: int,
    donor_id: int,
    sentences: list[CitedSentence],
) -> None:
    """Persist an explanation log."""
    body = json.dumps(
        [
            {
                "text": item.text,
                "field": item.field,
                "value": item.value,
                "kind": item.kind,
            }
            for item in sentences
        ]
    )
    db.add(
        LlmLog(
            user_id=user_id,
            donor_id=donor_id,
            body=body,
            created_at=datetime.now(timezone.utc),
        )
    )


def latest_explanation(db: Session, user_id: int, donor_id: int) -> list[CitedSentence]:
    """Return the newest stored explanation for a pair."""
    row = db.scalars(
        select(LlmLog)
        .where(LlmLog.user_id == user_id, LlmLog.donor_id == donor_id)
        .order_by(LlmLog.id.desc())
    ).first()
    if row is None:
        return []
    payload = json.loads(row.body)
    return [
        CitedSentence(item["text"], item["field"], item["value"], item["kind"])
        for item in payload
    ]


def list_shortlist(db: Session, couple_user_id: int) -> list[ShortlistItem]:
    """List shortlist rows for a couple, oldest first."""
    return list(
        db.scalars(
            select(ShortlistItem)
            .where(ShortlistItem.couple_user_id == couple_user_id)
            .order_by(ShortlistItem.id)
        )
    )


def shortlist_donor_ids(db: Session, couple_user_id: int) -> set[int]:
    """Return the set of shortlisted donor ids for a couple."""
    return {row.donor_id for row in list_shortlist(db, couple_user_id)}


def add_to_shortlist(db: Session, couple_user_id: int, donor_id: int) -> str | None:
    """Add a donor to the couple shortlist.

    Args:
        db: Open session.
        couple_user_id: Couple account id.
        donor_id: Confirmed donor id.

    Returns:
        An error message, or None on success.
    """
    donor = db.get(Donor, donor_id)
    if donor is None or not donor.catalog_confirmed:
        return "That donor is not on the confirmed inventory."
    existing = shortlist_donor_ids(db, couple_user_id)
    if donor_id in existing:
        return None
    if len(existing) >= MAX_SHORTLIST:
        return f"Shortlist is limited to {MAX_SHORTLIST} candidates for one visit."
    db.add(ShortlistItem(couple_user_id=couple_user_id, donor_id=donor_id))
    return None


def remove_from_shortlist(db: Session, couple_user_id: int, donor_id: int) -> None:
    """Remove a donor from the couple shortlist if present."""
    db.execute(
        delete(ShortlistItem).where(
            ShortlistItem.couple_user_id == couple_user_id,
            ShortlistItem.donor_id == donor_id,
        )
    )


def counselor_display_name(db: Session, counselor: User) -> str:
    """Return a display name for booking lists."""
    profile = get_or_create_counselor_profile(db, counselor.id)
    name = profile.display_name.strip()
    return name or counselor.email


def _as_utc(value: datetime) -> datetime:
    """Treat naive SQLite timestamps as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def booked_slot_ids(db: Session) -> set[int]:
    """Return slot ids held by a still-booked appointment."""
    return set(
        db.scalars(
            select(Appointment.slot_id).where(Appointment.status == APPOINTMENT_BOOKED)
        )
    )


def open_future_slots(
    db: Session, now: datetime | None = None
) -> list[AvailabilitySlot]:
    """List future slots that are not yet booked.

    Args:
        db: Open session.
        now: Clock override for tests.

    Returns:
        Open slots ordered by start time.
    """
    clock = _as_utc(now or datetime.now(timezone.utc))
    taken = booked_slot_ids(db)
    slots = list(
        db.scalars(
            select(AvailabilitySlot).order_by(
                AvailabilitySlot.starts_at, AvailabilitySlot.id
            )
        )
    )
    return [
        slot
        for slot in slots
        if slot.id not in taken and _as_utc(slot.starts_at) >= clock
    ]


def counselors_with_open_slots(
    db: Session, now: datetime | None = None
) -> list[tuple[User, CounselorProfile, list[AvailabilitySlot]]]:
    """Group open future slots by counselor.

    Args:
        db: Open session.
        now: Clock override for tests.

    Returns:
        Counselor, profile, and that counselor's open slots.
    """
    slots = open_future_slots(db, now=now)
    by_counselor: dict[int, list[AvailabilitySlot]] = {}
    for slot in slots:
        by_counselor.setdefault(slot.counselor_user_id, []).append(slot)
    rows: list[tuple[User, CounselorProfile, list[AvailabilitySlot]]] = []
    for counselor_id, open_slots in by_counselor.items():
        counselor = db.get(User, counselor_id)
        if counselor is None or counselor.role != "counselor":
            continue
        profile = get_or_create_counselor_profile(db, counselor.id)
        rows.append((counselor, profile, open_slots))
    rows.sort(
        key=lambda item: (
            (item[1].display_name or item[0].email).lower(),
            item[0].id,
        )
    )
    return rows


def slot_is_open(
    db: Session, slot: AvailabilitySlot, now: datetime | None = None
) -> bool:
    """Return True when the slot is in the future and has no booked appointment."""
    clock = _as_utc(now or datetime.now(timezone.utc))
    if _as_utc(slot.starts_at) < clock:
        return False
    taken = db.scalars(
        select(Appointment.id).where(
            Appointment.slot_id == slot.id,
            Appointment.status == APPOINTMENT_BOOKED,
        )
    ).first()
    return taken is None


def active_booked_appointment(db: Session, couple_user_id: int) -> Appointment | None:
    """Return the couple's current booked visit, if any."""
    return db.scalars(
        select(Appointment)
        .where(
            Appointment.couple_user_id == couple_user_id,
            Appointment.status == APPOINTMENT_BOOKED,
        )
        .order_by(Appointment.id)
    ).first()


def book_appointment(
    db: Session,
    couple: User,
    slot: AvailabilitySlot,
    now: datetime | None = None,
) -> Appointment | str:
    """Book a visit from the couple shortlist onto an open slot.

    Args:
        db: Open session.
        couple: Couple user.
        slot: Slot to book.
        now: Clock override for tests.

    Returns:
        The appointment, or an error string.
    """
    existing = active_booked_appointment(db, couple.id)
    if existing is not None:
        return (
            "You already have a booked visit. "
            "Cancel it before booking another slot."
        )
    if not slot_is_open(db, slot, now=now):
        return "That slot is no longer available."
    items = list_shortlist(db, couple.id)
    if not items:
        return "Add at least one candidate to the shortlist before booking."
    if len(items) > MAX_SHORTLIST:
        return f"Shortlist is limited to {MAX_SHORTLIST} candidates for one visit."
    donors: list[Donor] = []
    for item in items:
        donor = db.get(Donor, item.donor_id)
        if donor is None or not donor.catalog_confirmed:
            continue
        donors.append(donor)
    if not donors:
        return "Your shortlist has no confirmed donors left to book."
    appointment = Appointment(
        couple_user_id=couple.id,
        counselor_user_id=slot.counselor_user_id,
        slot_id=slot.id,
        status=APPOINTMENT_BOOKED,
        created_at=_as_utc(now or datetime.now(timezone.utc)).replace(tzinfo=None),
    )
    try:
        # * Savepoint so a unique-index race does not wipe shortlist mirror work.
        with db.begin_nested():
            db.add(appointment)
            db.flush()
            for donor in donors:
                db.add(
                    AppointmentDonor(
                        appointment_id=appointment.id,
                        donor_id=donor.id,
                        donor_code=donor.code,
                    )
                )
            db.flush()
    except IntegrityError:
        return "That slot is no longer available."
    return appointment


def cancel_appointment(db: Session, appointment: Appointment) -> str | None:
    """Cancel a booked visit and free its slot.

    Args:
        db: Open session.
        appointment: Visit to cancel.

    Returns:
        An error message, or None on success.
    """
    if appointment.status == APPOINTMENT_CANCELLED:
        return "That visit is already cancelled."
    if appointment.status != APPOINTMENT_BOOKED:
        return "Only a booked visit can be cancelled."
    appointment.status = APPOINTMENT_CANCELLED
    return None


def appointment_donor_ids(db: Session, appointment_id: int) -> list[int]:
    """Return donor ids booked on a visit, in snapshot order."""
    return [donor.id for donor in resolve_visit_donors(db, appointment_id)]


def resolve_visit_donors(db: Session, appointment_id: int) -> list[Donor]:
    """Live donor rows for a visit, re-bound by code when ids go stale.

    Args:
        db: Open session.
        appointment_id: Booked visit id.

    Returns:
        Donors in booking order. Missing codes are skipped.
    """
    rows = list(
        db.scalars(
            select(AppointmentDonor)
            .where(AppointmentDonor.appointment_id == appointment_id)
            .order_by(AppointmentDonor.id)
        )
    )
    donors: list[Donor] = []
    for row in rows:
        if not row.donor_code:
            donor = db.get(Donor, row.donor_id)
            if donor is not None:
                row.donor_code = donor.code
        donor = db.get(Donor, row.donor_id)
        if donor is None and row.donor_code:
            donor = db.scalars(
                select(Donor).where(Donor.code == row.donor_code)
            ).first()
            if donor is not None:
                # * /tmp reseed reused codes under new primary keys.
                row.donor_id = donor.id
        if donor is not None:
            donors.append(donor)
    return donors


def sentences_to_json(sentences: list[CitedSentence]) -> str:
    """Serialize cited sentences for storage."""
    return json.dumps(
        [
            {
                "text": item.text,
                "field": item.field,
                "value": item.value,
                "kind": item.kind,
            }
            for item in sentences
        ]
    )


def sentences_from_json(body: str) -> list[CitedSentence]:
    """Parse cited sentences from storage."""
    payload = json.loads(body)
    return [
        CitedSentence(item["text"], item["field"], item["value"], item["kind"])
        for item in payload
    ]


def store_candidate_report(
    db: Session,
    appointment_id: int,
    donor_id: int,
    sentences: list[CitedSentence],
) -> CandidateReport:
    """Insert or overwrite the counselor report for one visit candidate."""
    existing = db.scalars(
        select(CandidateReport).where(
            CandidateReport.appointment_id == appointment_id,
            CandidateReport.donor_id == donor_id,
        )
    ).first()
    body = sentences_to_json(sentences)
    now = datetime.now(timezone.utc)
    if existing is None:
        row = CandidateReport(
            appointment_id=appointment_id,
            donor_id=donor_id,
            body=body,
            created_at=now,
        )
        db.add(row)
        db.flush()
        return row
    existing.body = body
    existing.created_at = now
    return existing


def latest_candidate_report(
    db: Session, appointment_id: int, donor_id: int
) -> list[CitedSentence]:
    """Return the stored counselor report sentences, or an empty list."""
    row = db.scalars(
        select(CandidateReport).where(
            CandidateReport.appointment_id == appointment_id,
            CandidateReport.donor_id == donor_id,
        )
    ).first()
    if row is None:
        return []
    return sentences_from_json(row.body)


def run_candidate_report(
    db: Session,
    appointment: Appointment,
    donor: Donor,
    settings: Settings,
) -> list[CitedSentence]:
    """Build and store a live-profile counselor report for one candidate.

    Args:
        db: Open session.
        appointment: Booked visit.
        donor: Live donor row that belongs to the visit.
        settings: Process settings.

    Returns:
        Cited report sentences.
    """
    couple = db.get(User, appointment.couple_user_id)
    if couple is None:
        raise ValueError("Couple account is missing.")
    result = score_donor_for_couple(db, couple, donor, settings)
    packet = build_counselor_packet(
        donor=donor,
        donor_carriers=list_carriers(db, "donor", donor.id),
        couple_profile=get_or_create_profile(db, couple.id),
        survey=get_or_create_survey(db, couple.id),
        history=db.get(PriorHistory, couple.id),
        couple_carriers=list_carriers(db, "couple", couple.id),
        result=result,
    )
    sentences = generate_counselor_report(packet, settings)
    store_candidate_report(db, appointment.id, donor.id, sentences)
    return sentences


def delete_appointment_tree(db: Session, appointment_ids: list[int]) -> None:
    """Delete reports, visit donors, and appointments for the given ids."""
    if not appointment_ids:
        return
    db.execute(
        delete(CandidateReport).where(
            CandidateReport.appointment_id.in_(appointment_ids)
        )
    )
    db.execute(
        delete(AppointmentDonor).where(
            AppointmentDonor.appointment_id.in_(appointment_ids)
        )
    )
    db.execute(delete(Appointment).where(Appointment.id.in_(appointment_ids)))


def delete_donor(db: Session, donor: Donor) -> None:
    """Remove a donor and rows that reference it (shortlist, visit links, carriers)."""
    donor_id = donor.id
    visit_ids = list(
        db.scalars(
            select(AppointmentDonor.appointment_id).where(
                AppointmentDonor.donor_id == donor_id
            )
        )
    )
    if visit_ids:
        delete_appointment_tree(db, visit_ids)
    db.execute(delete(ShortlistItem).where(ShortlistItem.donor_id == donor_id))
    db.execute(
        delete(Carrier).where(
            Carrier.subject_type == "donor", Carrier.subject_id == donor_id
        )
    )
    db.execute(delete(MotilitySample).where(MotilitySample.donor_id == donor_id))
    db.delete(donor)


def delete_account(db: Session, user: User) -> None:
    """Delete genetic fields, surveys, visits, donors, and LLM logs."""
    if user.role == "couple":
        appointment_ids = list(
            db.scalars(
                select(Appointment.id).where(Appointment.couple_user_id == user.id)
            )
        )
        delete_appointment_tree(db, appointment_ids)
        db.execute(delete(ShortlistItem).where(ShortlistItem.couple_user_id == user.id))
        db.execute(
            delete(Carrier).where(
                Carrier.subject_type == "couple", Carrier.subject_id == user.id
            )
        )
        db.execute(delete(CoupleProfile).where(CoupleProfile.user_id == user.id))
        db.execute(delete(CoupleSurvey).where(CoupleSurvey.user_id == user.id))
        db.execute(delete(PriorHistory).where(PriorHistory.user_id == user.id))
    elif user.role == "counselor":
        appointment_ids = list(
            db.scalars(
                select(Appointment.id).where(Appointment.counselor_user_id == user.id)
            )
        )
        delete_appointment_tree(db, appointment_ids)
        db.execute(
            delete(AvailabilitySlot).where(
                AvailabilitySlot.counselor_user_id == user.id
            )
        )
        db.execute(delete(CounselorProfile).where(CounselorProfile.user_id == user.id))
    else:
        donor_ids = list(
            db.scalars(select(Donor.id).where(Donor.bank_user_id == user.id))
        )
        if donor_ids:
            # * Visit rows that point at these donors must go before donor delete.
            visit_ids = list(
                db.scalars(
                    select(AppointmentDonor.appointment_id).where(
                        AppointmentDonor.donor_id.in_(donor_ids)
                    )
                )
            )
            delete_appointment_tree(db, visit_ids)
            db.execute(
                delete(ShortlistItem).where(ShortlistItem.donor_id.in_(donor_ids))
            )
            db.execute(
                delete(Carrier).where(
                    Carrier.subject_type == "donor", Carrier.subject_id.in_(donor_ids)
                )
            )
            db.execute(delete(Donor).where(Donor.id.in_(donor_ids)))
    db.execute(delete(LlmLog).where(LlmLog.user_id == user.id))
    db.delete(user)


def find_ranked_donor(
    rows: list[tuple[Donor, MatchResult]],
    donor_id: int,
) -> tuple[Donor, MatchResult] | None:
    """Find one donor inside an already ranked list."""
    for donor, result in rows:
        if donor.id == donor_id:
            return donor, result
    return None


def home_path_for(user: User) -> str:
    """Return the post-login landing path for a role."""
    if user.role == "bank":
        return "/bank"
    if user.role == "counselor":
        return "/counselor"
    return "/couple/history"
