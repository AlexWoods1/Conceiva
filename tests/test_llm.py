"""Tests for cited match explanations."""

import json
from pathlib import Path

import httpx

from app.config import Settings
from app.llm import (
    build_explain_packet,
    deterministic_explain,
    explain,
    filter_model_sentences,
)
from app.matching import MatchResult, PersonGenetics, Preference, Reason


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


def _packet(hard_stop: bool = False):
    reasons = []
    if hard_stop:
        reasons.append(
            Reason(
                "hard_stop",
                "carriers.gene",
                "CFTR",
                "Both profiles list CFTR as a confirmed recessive carrier. This is a hard stop.",
            )
        )
    couple = PersonGenetics(
        "O", "negative", "Finnish", frozenset({"CFTR"}) if hard_stop else frozenset()
    )
    prefs = Preference("either", 10, "any")
    result = MatchResult(
        score=0 if hard_stop else 100, hard_stop=hard_stop, reasons=reasons
    )
    return build_explain_packet(
        couple=couple,
        prefs=prefs,
        result=result,
        donor_panel="ACMG-SF",
        known_conditions="email me at nurse@clinic.example",
        prior_pregnancies=1,
        prior_donors="",
        miscarriages=0,
    )


def test_build_explain_packet_redacts_notes_and_lists_missing_fields():
    packet = build_explain_packet(
        couple=PersonGenetics("", "", "", frozenset()),
        prefs=Preference("open", 10, "negative_required"),
        result=MatchResult(score=100, hard_stop=False),
        donor_panel="  ",
        known_conditions="",
        prior_pregnancies=None,
        prior_donors="",
        miscarriages=None,
    )

    assert [fact.field for fact in packet.facts] == ["couple.cmv_requirement"]
    assert packet.missing == [
        "donor.panel",
        "history.prior_pregnancies",
        "history.miscarriages",
        "history.prior_donors",
        "history.known_conditions",
        "couple.blood_type",
    ]


def test_build_explain_packet_keeps_zero_counts_and_redacts_conditions():
    packet = _packet()
    fields = {fact.field: fact.value for fact in packet.facts}

    assert fields["donor.panel"] == "ACMG-SF"
    assert fields["history.prior_pregnancies"] == "1"
    assert fields["history.miscarriages"] == "0"
    assert fields["history.known_conditions"] == "email me at [redacted-email]"
    assert "history.prior_donors" in packet.missing
    assert "clinical_notes" not in fields


def test_deterministic_explain_cites_facts_and_absences():
    sentences = deterministic_explain(_packet())

    assert any(
        item.field == "donor.panel" and item.kind == "record" for item in sentences
    )
    assert any(
        item.kind == "missing" and item.field == "history.prior_donors"
        for item in sentences
    )


def test_filter_model_sentences_drops_unknown_fields():
    packet = _packet()
    payload = {
        "sentences": [
            {"text": "Panel is listed.", "field": "donor.panel"},
            {"text": "Invented risk.", "field": "child.diagnosis"},
            {"text": "   ", "field": "history.miscarriages"},
            "not-a-sentence",
            {"text": "Model tried to fill this in.", "field": "history.prior_donors"},
        ]
    }

    kept = filter_model_sentences(payload, packet)

    assert [(item.field, item.text, item.value) for item in kept] == [
        ("donor.panel", "Panel is listed.", "ACMG-SF"),
        ("history.prior_donors", "Not in the record: history.prior_donors.", ""),
    ]
    assert filter_model_sentences({}, packet) == []


def test_explain_without_a_key_does_not_call_the_model():
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("The model client should stay unused.")

    packet = _packet()
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        sentences = explain(packet, _settings(), client)

    assert sentences == deterministic_explain(packet)


def _model_client(payload: dict | str, status_code: int = 200) -> httpx.Client:
    content = payload if isinstance(payload, str) else json.dumps(payload)

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://llm.example/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["model"] == "test-model"
        if status_code != 200:
            return httpx.Response(status_code, json={"error": "down"})
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}}]}
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_explain_keeps_a_hard_stop_the_model_omits(caplog):
    packet = _packet(hard_stop=True)
    settings = _settings(llm_api_key="test-key")
    with _model_client(
        {"sentences": [{"text": "Panel is listed.", "field": "donor.panel"}]}
    ) as client:
        sentences = explain(packet, settings, client)

    assert sentences[0].kind == "hard_stop"
    assert sentences[0].value == "CFTR"
    assert sentences[1].text == "Panel is listed."
    assert "cited no known fields" not in caplog.text


def test_explain_does_not_duplicate_a_hard_stop_the_model_cites():
    packet = _packet(hard_stop=True)
    payload = {
        "sentences": [
            {"text": "Shared CFTR carrier.", "field": "carriers.gene"},
            {"text": "Panel is listed.", "field": "donor.panel"},
        ]
    }
    with _model_client(payload) as client:
        sentences = explain(packet, _settings(llm_api_key="test-key"), client)

    hard_stops = [item for item in sentences if item.kind == "hard_stop"]
    assert len(hard_stops) == 1
    assert hard_stops[0].text == "Shared CFTR carrier."


def test_explain_falls_back_when_the_model_fails(caplog):
    packet = _packet()
    settings = _settings(llm_api_key="test-key")
    local = deterministic_explain(packet)

    with _model_client(
        {"sentences": [{"text": "Nope.", "field": "not.real"}]}
    ) as client:
        assert explain(packet, settings, client) == local
    assert "cited no known fields" in caplog.text

    caplog.clear()
    with _model_client("not-json") as client:
        assert explain(packet, settings, client) == local
    assert "LLM explanation failed" in caplog.text

    caplog.clear()
    with _model_client({"sentences": []}, status_code=503) as client:
        assert explain(packet, settings, client) == local
    assert "LLM explanation failed" in caplog.text


def test_explain_rejects_a_json_array(caplog):
    packet = _packet()
    with _model_client([]) as client:
        sentences = explain(packet, _settings(llm_api_key="test-key"), client)

    assert sentences == deterministic_explain(packet)
    assert "LLM explanation failed" in caplog.text


def test_model_sentence_uses_the_record_value():
    packet = _packet()
    payload = {"sentences": [{"text": "Panel is listed.", "field": "donor.panel"}]}
    with _model_client(payload) as client:
        sentences = explain(packet, _settings(llm_api_key="test-key"), client)

    panel = next(item for item in sentences if item.field == "donor.panel")
    assert panel.text == "Panel is listed."
    assert panel.value == "ACMG-SF"
    assert panel.kind == "record"
