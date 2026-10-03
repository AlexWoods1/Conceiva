"""Database operations shared by the site."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import Settings
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
    Carrier,
    CoupleProfile,
    CoupleSurvey,
    Donor,
    LlmLog,
    PriorHistory,
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
        family_limit=survey.family_limit,
        cmv_requirement=survey.cmv_requirement or "any",
    )
    donors = list(db.scalars(select(Donor).where(Donor.catalog_confirmed.is_(True))))
    scored: list[tuple[Donor, MatchResult]] = []
    for donor in donors:
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
        )
        scored.append((donor, score_match(person, prefs, offer, face_score=face)))
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


def delete_account(db: Session, user: User) -> None:
    """Delete genetic fields, surveys, photos keys, donors, and LLM logs."""
    if user.role == "couple":
        db.execute(
            delete(Carrier).where(
                Carrier.subject_type == "couple", Carrier.subject_id == user.id
            )
        )
        db.execute(delete(CoupleProfile).where(CoupleProfile.user_id == user.id))
        db.execute(delete(CoupleSurvey).where(CoupleSurvey.user_id == user.id))
        db.execute(delete(PriorHistory).where(PriorHistory.user_id == user.id))
    else:
        donor_ids = list(
            db.scalars(select(Donor.id).where(Donor.bank_user_id == user.id))
        )
        if donor_ids:
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
