"""Explainable donor rank.

A shared confirmed recessive carrier is a hard stop. Survey fields are soft
weights. Ancestry overlap is context and does not change the score.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.constants import (
    CMV_PENALTY,
    CMV_UNKNOWN_PENALTY,
    CONFIRMED_CARRIER,
    FAMILY_LIMIT_PENALTY,
    ID_RELEASE_PENALTY,
    MOTILITY_PENALTY,
    QUARANTINE_PENALTY,
)


@dataclass(frozen=True)
class Reason:
    """One cited factor on a match."""

    kind: str
    field: str
    value: str
    text: str


@dataclass(frozen=True)
class PersonGenetics:
    """Couple blood type, ancestry, and confirmed carrier genes."""

    blood_type: str
    rh: str
    ancestry: str
    genes: frozenset[str]


@dataclass(frozen=True)
class Preference:
    """Couple preference answers that apply soft weights."""

    id_release: str
    family_limit: int
    cmv_requirement: str


@dataclass(frozen=True)
class DonorOffer:
    """Donor fields the scorer is allowed to see."""

    code: str
    blood_type: str
    rh: str
    ancestry: str
    genes: frozenset[str]
    panel: str
    cmv: str
    quarantine: str
    family_limit: int
    id_release_policy: str
    motility_below_reference: bool = False


@dataclass
class MatchResult:
    """Medical rank for one donor. Face score is carried beside it."""

    score: int
    hard_stop: bool
    reasons: list[Reason] = field(default_factory=list)
    face_score: int | None = None


def confirmed_genes(rows: list[tuple[str, str]]) -> frozenset[str]:
    """Collect gene symbols with confirmed carrier zygosity.

    Args:
        rows: Pairs of gene symbol and zygosity.

    Returns:
        Uppercased gene symbols. Unknown zygosity is excluded.
    """
    genes = {
        gene.strip().upper()
        for gene, zygosity in rows
        if zygosity in CONFIRMED_CARRIER and gene.strip()
    }
    return frozenset(genes)


def _tokens(value: str) -> set[str]:
    parts = re.split(r"[,/;|]", value.lower())
    return {part.strip() for part in parts if part.strip()}


def ancestry_overlaps(left: str, right: str) -> bool:
    """Return True when two ancestry strings share a token.

    Args:
        left: Couple ancestry text.
        right: Donor ancestry text.

    Returns:
        True on a shared token. This does not change rank.
    """
    if not left.strip() or not right.strip():
        return False
    return bool(_tokens(left) & _tokens(right))


def score_match(
    couple: PersonGenetics,
    prefs: Preference,
    donor: DonorOffer,
    face_score: int | None = None,
) -> MatchResult:
    """Score one donor against a couple.

    Args:
        couple: Couple genetics.
        prefs: Survey preferences.
        donor: Donor offer.
        face_score: Optional non-clinical resemblance stub.

    Returns:
        A match result. Hard stops score 0 and stay visible. Face score is
        stored and is not added to the medical score.
    """
    reasons: list[Reason] = []
    score = 100
    shared = couple.genes & donor.genes
    for gene in sorted(shared):
        reasons.append(
            Reason(
                kind="hard_stop",
                field="carriers.gene",
                value=gene,
                text=(
                    f"Both profiles list {gene} as a confirmed recessive carrier. "
                    "This is a hard stop."
                ),
            )
        )

    if couple.rh == "negative" and donor.rh == "positive":
        reasons.append(
            Reason(
                kind="rh_flag",
                field="donor.rh",
                value=donor.rh,
                text=(
                    "Rh flag: the couple is Rh negative and the donor is Rh positive. "
                    "This is informational and is not a block."
                ),
            )
        )

    if couple.blood_type and donor.blood_type:
        reasons.append(
            Reason(
                kind="blood",
                field="couple.blood_type",
                value=f"{couple.blood_type} {couple.rh} / {donor.blood_type} {donor.rh}",
                text=(
                    f"Blood types on record: couple {couple.blood_type} {couple.rh}, "
                    f"donor {donor.blood_type} {donor.rh}."
                ),
            )
        )

    if prefs.id_release != "either" and donor.id_release_policy not in {
        prefs.id_release,
        "either",
    }:
        score -= ID_RELEASE_PENALTY
        reasons.append(
            Reason(
                kind="survey",
                field="donor.id_release_policy",
                value=donor.id_release_policy,
                text=(
                    f"ID-release preference is {prefs.id_release}; "
                    f"donor policy is {donor.id_release_policy}."
                ),
            )
        )

    if donor.family_limit < prefs.family_limit:
        score -= FAMILY_LIMIT_PENALTY
        reasons.append(
            Reason(
                kind="survey",
                field="donor.family_limit",
                value=str(donor.family_limit),
                text=(
                    f"Donor family limit is {donor.family_limit}; "
                    f"the couple entered {prefs.family_limit}."
                ),
            )
        )

    if prefs.cmv_requirement == "negative_required" and donor.cmv == "positive":
        score -= CMV_PENALTY
        reasons.append(
            Reason(
                kind="survey",
                field="donor.cmv",
                value=donor.cmv,
                text="The couple requires CMV negative. The donor record is CMV positive.",
            )
        )
    elif prefs.cmv_requirement == "negative_required" and donor.cmv == "unknown":
        score -= CMV_UNKNOWN_PENALTY
        reasons.append(
            Reason(
                kind="survey",
                field="donor.cmv",
                value=donor.cmv,
                text="The couple requires CMV negative. Donor CMV status is not in a confirmed state.",
            )
        )

    if donor.quarantine == "in_quarantine":
        score -= QUARANTINE_PENALTY
        reasons.append(
            Reason(
                kind="survey",
                field="donor.quarantine",
                value=donor.quarantine,
                text="The donor is still in quarantine on the bank survey.",
            )
        )

    if donor.motility_below_reference:
        score -= MOTILITY_PENALTY
        reasons.append(
            Reason(
                kind="survey",
                field="donor.motility_below_reference",
                value="below_reference",
                text=(
                    "The motility analysis for this donor came back below the WHO "
                    "lower reference limits. This is a research-demo measurement, "
                    "not a clinical semen analysis."
                ),
            )
        )

    if ancestry_overlaps(couple.ancestry, donor.ancestry):
        reasons.append(
            Reason(
                kind="ancestry_context",
                field="donor.ancestry",
                value=donor.ancestry,
                text=(
                    "Ancestry tokens overlap. This is allele-frequency context only "
                    "and does not change the rank."
                ),
            )
        )

    hard_stop = bool(shared)
    if hard_stop:
        score = 0
    score = max(score, 0)
    return MatchResult(
        score=score, hard_stop=hard_stop, reasons=reasons, face_score=face_score
    )


def rank_results(rows: list[tuple[str, MatchResult]]) -> list[tuple[str, MatchResult]]:
    """Sort donors with clear medical matches ahead of hard stops.

    Args:
        rows: Donor codes paired with match results.

    Returns:
        Rows ordered by hard stop, then score descending, then code.
    """
    return sorted(rows, key=lambda item: (item[1].hard_stop, -item[1].score, item[0]))
