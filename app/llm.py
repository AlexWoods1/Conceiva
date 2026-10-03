"""Match-page explanations that can cite only known record fields."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

import httpx

from app.config import Settings
from app.matching import MatchResult, PersonGenetics, Preference
from app.security import redact_free_text

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Fact:
    """One field the model is allowed to mention."""

    field: str
    value: str
    text: str
    kind: str


@dataclass
class ExplainPacket:
    """Facts retrieved for one couple-donor pair, plus absent fields."""

    facts: list[Fact] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CitedSentence:
    """A sentence and the record field that supports it."""

    text: str
    field: str
    value: str
    kind: str


def build_explain_packet(
    couple: PersonGenetics,
    prefs: Preference,
    result: MatchResult,
    donor_panel: str,
    known_conditions: str,
    prior_pregnancies: int | None,
    prior_donors: str,
    miscarriages: int | None,
) -> ExplainPacket:
    """Build the only context an explanation may use.

    Clinical notes are not an argument. They stay in the database and out of
    the model context.

    Args:
        couple: Couple genetics.
        prefs: Survey preferences.
        result: Scorer output.
        donor_panel: Panel name from the bank survey.
        known_conditions: Family conditions before redaction.
        prior_pregnancies: Count, or None when history was never saved.
        prior_donors: Prior donor text before redaction.
        miscarriages: Count, or None when history was never saved.

    Returns:
        Facts and missing field names.
    """
    facts = [
        Fact(reason.field, reason.value, reason.text, reason.kind)
        for reason in result.reasons
    ]
    missing: list[str] = []
    if donor_panel.strip():
        facts.append(
            Fact(
                "donor.panel",
                donor_panel.strip(),
                f"The bank survey lists panel {donor_panel.strip()}.",
                "record",
            )
        )
    else:
        missing.append("donor.panel")

    if prior_pregnancies is None:
        missing.append("history.prior_pregnancies")
    else:
        facts.append(
            Fact(
                "history.prior_pregnancies",
                str(prior_pregnancies),
                f"Prior pregnancies on record: {prior_pregnancies}.",
                "record",
            )
        )
    if miscarriages is None:
        missing.append("history.miscarriages")
    else:
        facts.append(
            Fact(
                "history.miscarriages",
                str(miscarriages),
                f"Miscarriages on record: {miscarriages}.",
                "record",
            )
        )
    redacted_donors = redact_free_text(prior_donors)
    if redacted_donors:
        facts.append(
            Fact(
                "history.prior_donors",
                redacted_donors,
                f"Prior donors on record: {redacted_donors}.",
                "record",
            )
        )
    else:
        missing.append("history.prior_donors")
    redacted_conditions = redact_free_text(known_conditions)
    if redacted_conditions:
        facts.append(
            Fact(
                "history.known_conditions",
                redacted_conditions,
                f"Known family conditions on record: {redacted_conditions}.",
                "record",
            )
        )
    else:
        missing.append("history.known_conditions")

    if prefs.cmv_requirement:
        facts.append(
            Fact(
                "couple.cmv_requirement",
                prefs.cmv_requirement,
                f"Couple CMV requirement: {prefs.cmv_requirement}.",
                "record",
            )
        )
    if not couple.blood_type:
        missing.append("couple.blood_type")
    return ExplainPacket(facts=facts, missing=missing)


def deterministic_explain(packet: ExplainPacket) -> list[CitedSentence]:
    """Explain a match from retrieved facts only.

    Args:
        packet: Allowed facts and missing field names.

    Returns:
        Cited sentences. Missing fields say they are not in the record.
    """
    sentences = [
        CitedSentence(fact.text, fact.field, fact.value, fact.kind)
        for fact in packet.facts
    ]
    for name in packet.missing:
        sentences.append(
            CitedSentence(f"Not in the record: {name}.", name, "", "missing")
        )
    return sentences


def filter_model_sentences(payload: dict, packet: ExplainPacket) -> list[CitedSentence]:
    """Drop model sentences that cite a field outside the packet.

    Args:
        payload: JSON object with a ``sentences`` list.
        packet: Allowed facts and missing names.

    Returns:
        Sentences whose field is a known fact or a known absence.
    """
    allowed = {fact.field: fact for fact in packet.facts}
    missing = set(packet.missing)
    kept: list[CitedSentence] = []
    for item in payload.get("sentences", []):
        if not isinstance(item, dict):
            continue
        field_name = str(item.get("field", ""))
        text = str(item.get("text", "")).strip()
        if field_name in missing:
            kept.append(
                CitedSentence(
                    f"Not in the record: {field_name}.", field_name, "", "missing"
                )
            )
            continue
        fact = allowed.get(field_name)
        if fact is None or not text:
            continue
        kept.append(CitedSentence(text, fact.field, fact.value, fact.kind))
    return kept


def _merge_hard_stops(
    model_sentences: list[CitedSentence],
    local_sentences: list[CitedSentence],
) -> list[CitedSentence]:
    """Keep every hard-stop sentence even when the model omits it."""
    present = {(item.field, item.value) for item in model_sentences}
    prefix = [
        item
        for item in local_sentences
        if item.kind == "hard_stop" and (item.field, item.value) not in present
    ]
    return prefix + model_sentences


def _call_llm(packet: ExplainPacket, settings: Settings, client: httpx.Client) -> dict:
    """Call an OpenAI-compatible chat endpoint and parse a JSON object."""
    fact_payload = [
        {"field": fact.field, "value": fact.value, "text": fact.text}
        for fact in packet.facts
    ]
    body = {
        "model": settings.llm_model,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "You explain one donor match for decision support. "
                    "Use only the JSON facts. Every sentence must include a field id from those facts. "
                    "If a field is missing, say it is not in the record. "
                    "Do not diagnose. Do not predict a child's appearance. "
                    "Do not rank by race, ancestry, or attractiveness. "
                    'Return {"sentences": [{"text": "...", "field": "field.id"}]}.'
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {"facts": fact_payload, "missing": packet.missing}
                ),
            },
        ],
    }
    response = client.post(
        f"{settings.llm_base_url}/chat/completions",
        headers={"Authorization": f"Bearer {settings.llm_api_key}"},
        json=body,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise ValueError("Model response was not a JSON object.")
    return parsed


def explain(
    packet: ExplainPacket,
    settings: Settings,
    client: httpx.Client | None = None,
) -> list[CitedSentence]:
    """Explain a match, using the live model only when a key is configured.

    Args:
        packet: Retrieved facts.
        settings: Process settings.
        client: Optional HTTP client. Tests pass a mock transport.

    Returns:
        Cited sentences. Falls back to the record text when the model is absent
        or returns fields that are not in the packet.
    """
    local = deterministic_explain(packet)
    if not settings.llm_api_key:
        return local
    http = client or httpx.Client(timeout=20.0)
    close_client = client is None
    try:
        payload = _call_llm(packet, settings, http)
        checked = filter_model_sentences(payload, packet)
        if not checked:
            logger.warning(
                "LLM explanation cited no known fields; using the record text."
            )
            return local
        return _merge_hard_stops(checked, local)
    except (httpx.HTTPError, KeyError, ValueError, json.JSONDecodeError) as exc:
        detail = type(exc).__name__
        if isinstance(exc, httpx.HTTPStatusError):
            body = (exc.response.text or "")[:300]
            detail = f"{detail} {exc.response.status_code}: {body}"
        logger.warning("LLM explanation failed (%s); using the record text.", detail)
        return local
    finally:
        if close_client:
            http.close()
