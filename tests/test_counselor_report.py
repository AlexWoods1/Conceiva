"""Tests for counselor briefing packets and live-model reports."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from app.config import Settings
from app.counselor_report import build_counselor_packet, generate_counselor_report
from app.llm import deterministic_explain
from app.matching import MatchResult, Reason
from app.models import Carrier, CoupleProfile, CoupleSurvey, Donor, PriorHistory


def _settings(**overrides) -> Settings:
    values = {
        "database_path": Path("unused.db"),
        "session_secret": "test",
        "llm_api_key": "",
        "llm_base_url": "https://llm.example/v1",
        "llm_model": "test-model",
        "enable_face_compare": False,
        "seed_on_empty": False,
    }
    values.update(overrides)
    return Settings(**values)


def _donor(**overrides) -> Donor:
    values = {
        "bank_user_id": 1,
        "code": "DN-1",
        "blood_type": "O",
        "rh": "negative",
        "ancestry": "Korean",
        "panel": "ACMG-SF",
        "cmv": "negative",
        "quarantine": "cleared",
        "family_limit": 10,
        "id_release_policy": "open",
        "catalog_source_url": "",
        "catalog_confirmed": True,
        "motility_total_pct": None,
        "motility_progressive_pct": None,
        "motility_video_url": "",
        "motility_below_reference": False,
    }
    values.update(overrides)
    return Donor(**values)


def _profile(**overrides) -> CoupleProfile:
    values = {"user_id": 1, "blood_type": "O", "rh": "negative", "adult_photo_key": ""}
    values.update(overrides)
    return CoupleProfile(**values)


def _survey(**overrides) -> CoupleSurvey:
    values = {
        "user_id": 1,
        "ancestry": "Finnish",
        "cmv_requirement": "any",
        "clinical_notes": "",
        "id_release": "either",
        "family_limit": 10,
    }
    values.update(overrides)
    return CoupleSurvey(**values)


def _history(**overrides) -> PriorHistory:
    values = {
        "user_id": 1,
        "prior_pregnancies": 1,
        "prior_donors": "",
        "miscarriages": 0,
        "known_conditions": "",
    }
    values.update(overrides)
    return PriorHistory(**values)


def _hard_stop_result() -> MatchResult:
    return MatchResult(
        score=0,
        hard_stop=True,
        reasons=[
            Reason(
                "hard_stop",
                "carriers.gene",
                "CFTR",
                "Both profiles list CFTR as a confirmed recessive carrier. This is a hard stop.",
            )
        ],
    )


def _packet(**overrides):
    values = {
        "donor": _donor(),
        "donor_carriers": [],
        "couple_profile": _profile(),
        "survey": _survey(),
        "history": _history(),
        "couple_carriers": [],
        "result": MatchResult(score=100, hard_stop=False),
    }
    values.update(overrides)
    return build_counselor_packet(**values)


def _model_client(payload: dict | str | list, status_code: int = 200) -> httpx.Client:
    content = payload if isinstance(payload, str) else json.dumps(payload)

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://llm.example/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key"
        if status_code != 200:
            return httpx.Response(status_code, json={"error": "down"})
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}}]}
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_build_counselor_packet_includes_match_reasons_and_score():
    packet = _packet(result=_hard_stop_result())
    fields = {fact.field: fact.value for fact in packet.facts}

    assert fields["carriers.gene"] == "CFTR"
    assert fields["match.score"] == "0"
    assert fields["match.hard_stop"] == "yes"
    assert fields["donor.code"] == "DN-1"


def test_build_counselor_packet_marks_missing_motility_and_includes_values():
    missing = _packet(donor=_donor())
    assert "donor.motility_total_pct" in missing.missing
    assert "donor.motility_progressive_pct" in missing.missing
    assert "donor.motility_video_url" in missing.missing

    present = _packet(
        donor=_donor(
            motility_total_pct=45.5,
            motility_progressive_pct=32.0,
            motility_video_url="/videos/tracked.mp4",
        )
    )
    fields = {fact.field: fact.value for fact in present.facts}
    assert fields["donor.motility_total_pct"] == "45.5"
    assert fields["donor.motility_progressive_pct"] == "32.0"
    assert fields["donor.motility_video_url"] == "present"
    assert "donor.motility_total_pct" not in present.missing
    assert "donor.motility_progressive_pct" not in present.missing
    assert "donor.motility_video_url" not in present.missing


def test_build_counselor_packet_redacts_clinical_notes_and_history():
    packet = _packet(
        survey=_survey(
            clinical_notes="email nurse@clinic.example or call 415-555-0199"
        ),
        history=_history(prior_donors="prior bank 415-555-0100"),
    )
    fields = {fact.field: fact.value for fact in packet.facts}

    assert "[redacted-email]" in fields["couple.clinical_notes"]
    assert "[redacted-phone]" in fields["couple.clinical_notes"]
    assert "nurse@clinic.example" not in fields["couple.clinical_notes"]
    assert "[redacted-phone]" in fields["history.prior_donors"]
    assert "415-555-0100" not in fields["history.prior_donors"]


def test_build_counselor_packet_missing_dedupes_and_orders():
    packet = _packet(
        donor=_donor(ancestry="", cmv="unknown"),
        survey=_survey(ancestry=""),
    )

    assert packet.missing.count("donor.ancestry") == 1
    assert packet.missing.count("couple.ancestry") == 1
    assert "donor.cmv_detail" in packet.missing
    fields = {fact.field: fact.value for fact in packet.facts}
    assert fields["donor.cmv"] == "unknown"


def test_build_counselor_packet_carrier_unknown_zygosity_adds_detail_missing():
    empty = _packet(donor_carriers=[], couple_carriers=[])
    assert "donor.carriers" in empty.missing
    assert "couple.carriers" in empty.missing

    with_unknown = _packet(
        donor_carriers=[
            Carrier(
                subject_type="donor",
                subject_id=1,
                gene="HBB",
                zygosity="unknown",
                condition="Sickle cell disease",
            )
        ],
        couple_carriers=[
            Carrier(
                subject_type="couple",
                subject_id=1,
                gene="CFTR",
                zygosity="heterozygous",
                condition="Cystic fibrosis",
            )
        ],
    )
    assert "donor.carrier.HBB.zygosity_detail" in with_unknown.missing
    assert "donor.carriers" not in with_unknown.missing
    assert "couple.carriers" not in with_unknown.missing
    fields = {fact.field: fact.value for fact in with_unknown.facts}
    assert fields["donor.carrier.HBB"].startswith("unknown:")
    assert fields["couple.carrier.CFTR"].startswith("heterozygous:")


def test_generate_counselor_report_without_api_key_is_deterministic():
    packet = _packet()

    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("The model client should stay unused.")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        sentences = generate_counselor_report(packet, _settings(), client)

    assert sentences == deterministic_explain(packet)


def test_generate_counselor_report_merges_omitted_hard_stops():
    packet = _packet(result=_hard_stop_result())
    with _model_client(
        {"sentences": [{"text": "Panel is listed.", "field": "donor.panel"}]}
    ) as client:
        sentences = generate_counselor_report(
            packet, _settings(llm_api_key="test-key"), client
        )

    assert sentences[0].kind == "hard_stop"
    assert sentences[0].value == "CFTR"
    assert sentences[1].text == "Panel is listed."


def test_generate_counselor_report_falls_back_on_bad_model(caplog):
    packet = _packet()
    settings = _settings(llm_api_key="test-key")
    local = deterministic_explain(packet)

    with _model_client(
        {"sentences": [{"text": "Nope.", "field": "not.real"}]}
    ) as client:
        assert generate_counselor_report(packet, settings, client) == local
    assert "cited no known fields" in caplog.text

    caplog.clear()
    with _model_client("not-json") as client:
        assert generate_counselor_report(packet, settings, client) == local
    assert "Counselor LLM failed" in caplog.text

    caplog.clear()
    with _model_client({"sentences": []}, status_code=503) as client:
        assert generate_counselor_report(packet, settings, client) == local
    assert "Counselor LLM failed" in caplog.text
