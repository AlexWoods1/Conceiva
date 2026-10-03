"""Per-candidate counselor reports from the live couple and donor profile."""

from __future__ import annotations

import json
import logging

import httpx

from app.config import Settings
from app.constants import (
    CMV_REQUIREMENT_LABELS,
    CMV_STATUS_LABELS,
    ID_RELEASE_LABELS,
    QUARANTINE_LABELS,
    RH_LABELS,
    ZYGOSITY_LABELS,
)
from app.llm import (
    CitedSentence,
    ExplainPacket,
    Fact,
    deterministic_explain,
    filter_model_sentences,
)
from app.matching import MatchResult
from app.models import Carrier, CoupleProfile, CoupleSurvey, Donor, PriorHistory
from app.security import redact_free_text

logger = logging.getLogger(__name__)


def _label(mapping: dict[str, str], key: str) -> str:
    return mapping.get(key, key or "not recorded")


def _add(
    facts: list[Fact],
    missing: list[str],
    field: str,
    value: str,
    text: str,
    *,
    kind: str = "record",
    required: bool = True,
) -> None:
    cleaned = (value or "").strip()
    if cleaned:
        facts.append(Fact(field, cleaned, text, kind))
    elif required:
        missing.append(field)


def build_counselor_packet(
    donor: Donor,
    donor_carriers: list[Carrier],
    couple_profile: CoupleProfile,
    survey: CoupleSurvey,
    history: PriorHistory | None,
    couple_carriers: list[Carrier],
    result: MatchResult,
) -> ExplainPacket:
    """Build every fact a counselor report may cite for one candidate.

    Clinical notes are included after redaction. Couple-facing explanations
    omit them; this path is for the counselor session only.

    Args:
        donor: Live donor row.
        donor_carriers: Live donor carrier rows.
        couple_profile: Live couple profile.
        survey: Live couple survey.
        history: Live prior history, or None when never saved.
        couple_carriers: Live couple carrier rows.
        result: Scorer output for this pair.

    Returns:
        Facts and missing field names for cited report sentences.
    """
    facts: list[Fact] = [
        Fact(reason.field, reason.value, reason.text, reason.kind)
        for reason in result.reasons
    ]
    missing: list[str] = []

    facts.append(
        Fact(
            "donor.code",
            donor.code,
            f"Donor code on file: {donor.code}.",
            "record",
        )
    )
    _add(
        facts,
        missing,
        "donor.blood_type",
        donor.blood_type,
        f"Donor blood type: {donor.blood_type}.",
    )
    _add(
        facts,
        missing,
        "donor.rh",
        donor.rh,
        f"Donor Rh: {_label(RH_LABELS, donor.rh)}.",
    )
    _add(
        facts,
        missing,
        "donor.ancestry",
        donor.ancestry,
        f"Donor ancestry on file: {donor.ancestry}.",
        required=False,
    )
    if not donor.ancestry.strip():
        missing.append("donor.ancestry")
    _add(
        facts,
        missing,
        "donor.panel",
        donor.panel,
        f"Donor panel on file: {donor.panel}.",
    )
    facts.append(
        Fact(
            "donor.cmv",
            donor.cmv or "unknown",
            f"Donor CMV: {_label(CMV_STATUS_LABELS, donor.cmv or 'unknown')}.",
            "record",
        )
    )
    if (donor.cmv or "unknown") == "unknown":
        missing.append("donor.cmv_detail")
    facts.append(
        Fact(
            "donor.quarantine",
            donor.quarantine,
            f"Donor quarantine: {_label(QUARANTINE_LABELS, donor.quarantine)}.",
            "record",
        )
    )
    facts.append(
        Fact(
            "donor.family_limit",
            str(donor.family_limit),
            f"Donor family limit: {donor.family_limit}.",
            "record",
        )
    )
    facts.append(
        Fact(
            "donor.id_release_policy",
            donor.id_release_policy,
            f"Donor ID-release policy: {_label(ID_RELEASE_LABELS, donor.id_release_policy)}.",
            "record",
        )
    )
    facts.append(
        Fact(
            "donor.catalog_confirmed",
            "yes" if donor.catalog_confirmed else "no",
            "Donor catalog confirmation: "
            + ("confirmed." if donor.catalog_confirmed else "not confirmed."),
            "record",
        )
    )
    if donor.catalog_source_url.strip():
        facts.append(
            Fact(
                "donor.catalog_source_url",
                donor.catalog_source_url.strip()[:200],
                "A catalog source URL is on file for this donor.",
                "record",
            )
        )
    if donor.motility_total_pct is None:
        missing.append("donor.motility_total_pct")
    else:
        facts.append(
            Fact(
                "donor.motility_total_pct",
                f"{donor.motility_total_pct:.1f}",
                f"Donor total motility on file: {donor.motility_total_pct:.1f}%.",
                "record",
            )
        )
    if donor.motility_progressive_pct is None:
        missing.append("donor.motility_progressive_pct")
    else:
        facts.append(
            Fact(
                "donor.motility_progressive_pct",
                f"{donor.motility_progressive_pct:.1f}",
                f"Donor progressive motility on file: {donor.motility_progressive_pct:.1f}%.",
                "record",
            )
        )
    if donor.motility_video_url.strip():
        facts.append(
            Fact(
                "donor.motility_video_url",
                "present",
                "A motility video URL is on file for this donor.",
                "record",
            )
        )
    else:
        missing.append("donor.motility_video_url")

    if donor_carriers:
        for row in donor_carriers:
            field = f"donor.carrier.{row.gene}"
            zygosity = _label(ZYGOSITY_LABELS, row.zygosity)
            condition = row.condition.strip() or "condition not named"
            facts.append(
                Fact(
                    field,
                    f"{row.zygosity}:{condition}",
                    f"Donor carrier result {row.gene}: {zygosity}; {condition}.",
                    "record",
                )
            )
            if row.zygosity == "unknown":
                missing.append(f"donor.carrier.{row.gene}.zygosity_detail")
    else:
        missing.append("donor.carriers")

    _add(
        facts,
        missing,
        "couple.blood_type",
        couple_profile.blood_type,
        f"Couple blood type on file: {couple_profile.blood_type}.",
    )
    _add(
        facts,
        missing,
        "couple.rh",
        couple_profile.rh,
        f"Couple Rh on file: {_label(RH_LABELS, couple_profile.rh)}.",
    )
    _add(
        facts,
        missing,
        "couple.ancestry",
        survey.ancestry,
        f"Couple ancestry on file: {survey.ancestry}.",
        required=False,
    )
    if not survey.ancestry.strip():
        missing.append("couple.ancestry")
    facts.append(
        Fact(
            "couple.cmv_requirement",
            survey.cmv_requirement or "any",
            "Couple CMV requirement: "
            + _label(CMV_REQUIREMENT_LABELS, survey.cmv_requirement or "any")
            + ".",
            "record",
        )
    )
    _add(
        facts,
        missing,
        "couple.id_release",
        survey.id_release,
        f"Couple ID-release preference: {_label(ID_RELEASE_LABELS, survey.id_release)}.",
    )
    if survey.family_limit is None:
        missing.append("couple.family_limit")
    else:
        facts.append(
            Fact(
                "couple.family_limit",
                str(survey.family_limit),
                f"Couple family-limit preference: {survey.family_limit}.",
                "record",
            )
        )
    notes = redact_free_text(survey.clinical_notes)
    if notes:
        facts.append(
            Fact(
                "couple.clinical_notes",
                notes,
                f"Couple clinical notes on file: {notes}.",
                "record",
            )
        )
    else:
        missing.append("couple.clinical_notes")

    if history is None:
        missing.extend(
            [
                "history.prior_pregnancies",
                "history.miscarriages",
                "history.prior_donors",
                "history.known_conditions",
            ]
        )
    else:
        facts.append(
            Fact(
                "history.prior_pregnancies",
                str(history.prior_pregnancies),
                f"Prior pregnancies on record: {history.prior_pregnancies}.",
                "record",
            )
        )
        facts.append(
            Fact(
                "history.miscarriages",
                str(history.miscarriages),
                f"Miscarriages on record: {history.miscarriages}.",
                "record",
            )
        )
        prior_donors = redact_free_text(history.prior_donors)
        if prior_donors:
            facts.append(
                Fact(
                    "history.prior_donors",
                    prior_donors,
                    f"Prior donors on record: {prior_donors}.",
                    "record",
                )
            )
        else:
            missing.append("history.prior_donors")
        known = redact_free_text(history.known_conditions)
        if known:
            facts.append(
                Fact(
                    "history.known_conditions",
                    known,
                    f"Known family conditions on record: {known}.",
                    "record",
                )
            )
        else:
            missing.append("history.known_conditions")

    if couple_carriers:
        for row in couple_carriers:
            field = f"couple.carrier.{row.gene}"
            zygosity = _label(ZYGOSITY_LABELS, row.zygosity)
            condition = row.condition.strip() or "condition not named"
            facts.append(
                Fact(
                    field,
                    f"{row.zygosity}:{condition}",
                    f"Couple carrier result {row.gene}: {zygosity}; {condition}.",
                    "record",
                )
            )
            if row.zygosity == "unknown":
                missing.append(f"couple.carrier.{row.gene}.zygosity_detail")
    else:
        missing.append("couple.carriers")

    facts.append(
        Fact(
            "match.score",
            str(result.score),
            f"Medical rank score for this pair: {result.score}.",
            "record",
        )
    )
    facts.append(
        Fact(
            "match.hard_stop",
            "yes" if result.hard_stop else "no",
            "Hard stop for this pair: "
            + (
                "yes, a carrier conflict blocks this candidate."
                if result.hard_stop
                else "no."
            ),
            "record",
        )
    )

    # * Dedupe missing names while keeping first-seen order.
    seen: set[str] = set()
    ordered_missing: list[str] = []
    for name in missing:
        if name not in seen:
            seen.add(name)
            ordered_missing.append(name)
    return ExplainPacket(facts=facts, missing=ordered_missing)


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


def _call_counselor_llm(
    packet: ExplainPacket, settings: Settings, client: httpx.Client
) -> dict:
    """Call an OpenAI-compatible chat endpoint for a counselor report."""
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
                    "You write a briefing for a genetic counselor about one sperm-donor "
                    "candidate. Use only the JSON facts. Every sentence must include a "
                    "field id from those facts. If a field is missing, say it is not in "
                    "the record. Highlight carrier conflicts, gaps, and preference "
                    "mismatches so the counselor can give feedback in a meeting. "
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


def generate_counselor_report(
    packet: ExplainPacket,
    settings: Settings,
    client: httpx.Client | None = None,
) -> list[CitedSentence]:
    """Build a cited counselor report, using the live model when configured.

    Args:
        packet: Full-profile facts for one candidate.
        settings: Process settings.
        client: Optional HTTP client. Tests pass a mock transport.

    Returns:
        Cited sentences. Falls back to the deterministic draft when the model
        is absent or returns fields outside the packet.
    """
    local = deterministic_explain(packet)
    if not settings.llm_api_key:
        return local
    http = client or httpx.Client(timeout=20.0)
    close_client = client is None
    try:
        payload = _call_counselor_llm(packet, settings, http)
        checked = filter_model_sentences(payload, packet)
        if not checked:
            logger.warning(
                "Counselor LLM cited no known fields; using the record text."
            )
            return local
        return _merge_hard_stops(checked, local)
    except (httpx.HTTPError, KeyError, ValueError, json.JSONDecodeError) as exc:
        detail = type(exc).__name__
        if isinstance(exc, httpx.HTTPStatusError):
            body = (exc.response.text or "")[:300]
            detail = f"{detail} {exc.response.status_code}: {body}"
        logger.warning("Counselor LLM failed (%s); using the record text.", detail)
        return local
    finally:
        if close_client:
            http.close()
