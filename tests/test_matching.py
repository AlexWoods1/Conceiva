"""Tests for carrier hard stops and survey penalties."""

from app.constants import (
    CMV_PENALTY,
    CMV_UNKNOWN_PENALTY,
    FAMILY_LIMIT_PENALTY,
    ID_RELEASE_PENALTY,
    QUARANTINE_PENALTY,
)
from app.matching import (
    DonorOffer,
    MatchResult,
    PersonGenetics,
    Preference,
    ancestry_overlaps,
    confirmed_genes,
    rank_results,
    score_match,
)


def _couple(**overrides) -> PersonGenetics:
    values = {
        "blood_type": "O",
        "rh": "negative",
        "ancestry": "Finnish",
        "genes": frozenset(),
    }
    values.update(overrides)
    return PersonGenetics(**values)


def _prefs(**overrides) -> Preference:
    values = {"id_release": "either", "family_limit": 10, "cmv_requirement": "any"}
    values.update(overrides)
    return Preference(**values)


def _donor(**overrides) -> DonorOffer:
    values = {
        "code": "DN-1",
        "blood_type": "O",
        "rh": "negative",
        "ancestry": "Korean",
        "genes": frozenset(),
        "panel": "ACMG-SF",
        "cmv": "negative",
        "quarantine": "cleared",
        "family_limit": 10,
        "id_release_policy": "open",
    }
    values.update(overrides)
    return DonorOffer(**values)


def test_confirmed_genes_keeps_carrier_zygosity_only():
    genes = confirmed_genes(
        [
            (" cftr ", "heterozygous"),
            ("hbb", "unknown"),
            ("", "homozygous"),
            ("brca1", "homozygous"),
        ]
    )

    assert genes == frozenset({"CFTR", "BRCA1"})


def test_ancestry_overlap_uses_shared_tokens_and_does_not_score():
    assert ancestry_overlaps("", "Finnish") is False
    assert ancestry_overlaps("Finnish", "   ") is False
    assert ancestry_overlaps("Finnish, Korean", "Korean") is True
    assert ancestry_overlaps("Ashkenazi Jewish", "Jewish") is False
    assert ancestry_overlaps("Finnish / Korean", "korean") is True

    overlapped = score_match(
        _couple(ancestry="Finnish"),
        _prefs(),
        _donor(ancestry="Finnish"),
    )
    separate = score_match(_couple(), _prefs(), _donor())

    assert overlapped.score == separate.score == 100
    assert any(reason.kind == "ancestry_context" for reason in overlapped.reasons)
    assert all(reason.kind != "ancestry_context" for reason in separate.reasons)


def test_clear_match_scores_100_and_records_blood_types():
    result = score_match(_couple(), _prefs(), _donor(), face_score=99)

    assert result.score == 100
    assert result.hard_stop is False
    assert result.face_score == 99
    assert any(reason.kind == "blood" for reason in result.reasons)


def test_rh_mismatch_is_informational():
    result = score_match(_couple(rh="negative"), _prefs(), _donor(rh="positive"))

    assert result.score == 100
    assert result.hard_stop is False
    assert any(reason.kind == "rh_flag" for reason in result.reasons)


def test_survey_penalties_stack_and_stop_at_zero_only_for_a_hard_stop():
    penalized = score_match(
        _couple(),
        _prefs(id_release="open", family_limit=10, cmv_requirement="negative_required"),
        _donor(
            id_release_policy="anonymous",
            family_limit=5,
            cmv="positive",
            quarantine="in_quarantine",
        ),
    )
    expected = 100 - ID_RELEASE_PENALTY - FAMILY_LIMIT_PENALTY - CMV_PENALTY - QUARANTINE_PENALTY

    assert penalized.score == expected
    assert penalized.hard_stop is False

    unknown_cmv = score_match(
        _couple(),
        _prefs(cmv_requirement="negative_required"),
        _donor(cmv="unknown"),
    )
    assert unknown_cmv.score == 100 - CMV_UNKNOWN_PENALTY

    stopped = score_match(
        _couple(genes=confirmed_genes([("cftr", "heterozygous")])),
        _prefs(id_release="open"),
        _donor(
            genes=confirmed_genes([("CFTR", "heterozygous")]),
            id_release_policy="anonymous",
        ),
    )
    assert stopped.score == 0
    assert stopped.hard_stop is True
    assert any(reason.value == "CFTR" and reason.kind == "hard_stop" for reason in stopped.reasons)


def test_rank_results_puts_clear_matches_ahead_of_hard_stops():
    rows = [
        ("DN-2", MatchResult(score=80, hard_stop=False)),
        ("DN-9", MatchResult(score=0, hard_stop=True)),
        ("DN-1", MatchResult(score=80, hard_stop=False)),
        ("DN-3", MatchResult(score=90, hard_stop=False)),
    ]

    ordered = [code for code, _result in rank_results(rows)]

    assert ordered == ["DN-3", "DN-1", "DN-2", "DN-9"]
