"""Tests for form validation and catalog draft helpers."""

from types import SimpleNamespace

import pytest

from app.constants import PHOTO_KEYS
from app.routes import (
    _bounded_int,
    _carrier_fields,
    _choice,
    _clean_text,
    _donor_draft,
    _donor_from_draft,
    _drafts_from_session,
    _gene,
)
from app.scrape import CatalogDonor


def _draft(**overrides) -> dict[str, str]:
    values = {
        "code": "CAT-9",
        "ancestry": "Finnish",
        "blood_type": "O",
        "rh": "negative",
        "cmv": "negative",
        "panel": "ACMG-SF",
        "quarantine": "cleared",
        "family_limit": "10",
        "id_release_policy": "open",
    }
    values.update(overrides)
    return values


def test_choice_and_bounded_int_reject_values_outside_the_form():
    assert _choice("O", ("O", "A"), "blood type") == "O"
    with pytest.raises(ValueError, match="blood type"):
        _choice("Z", ("O", "A"), "blood type")

    assert _bounded_int("0", "Prior pregnancies", 0, 30) == 0
    with pytest.raises(ValueError, match="whole number"):
        _bounded_int("nope", "Prior pregnancies", 0, 30)
    with pytest.raises(ValueError, match="between 0 and 30"):
        _bounded_int("31", "Prior pregnancies", 0, 30)


def test_gene_and_clean_text_normalize_input():
    assert _gene("  cftr-1 ") == "CFTR-1"
    with pytest.raises(ValueError, match="gene code"):
        _gene("CF TR")
    with pytest.raises(ValueError, match="gene code"):
        _gene("")
    with pytest.raises(ValueError, match="gene code"):
        _gene("A" * 33)

    assert _clean_text("  too   many\nspaces  ", 8) == "too many"


def test_common_report_choice_sets_the_gene_code():
    gene, zygosity, condition = _carrier_fields(
        {"example": "cftr", "zygosity": "heterozygous", "gene": "", "condition": ""}
    )
    assert (gene, zygosity, condition) == ("CFTR", "heterozygous", "Cystic fibrosis")

    with pytest.raises(ValueError, match="condition name"):
        _carrier_fields({"example": "other", "zygosity": "heterozygous", "gene": "CFTR", "condition": ""})


def test_drafts_from_session_keep_dicts_only():
    mixed = SimpleNamespace(session={"catalog_draft": [{"code": "A"}, "x", None]})
    missing = SimpleNamespace(session={})
    wrong_type = SimpleNamespace(session={"catalog_draft": {"code": "A"}})

    assert _drafts_from_session(mixed) == [{"code": "A"}]
    assert _drafts_from_session(missing) == []
    assert _drafts_from_session(wrong_type) == []


def test_donor_draft_copies_catalog_fields():
    row = CatalogDonor(
        code="CAT-1",
        ancestry="Finnish",
        blood_type="O",
        rh="negative",
        cmv="negative",
        panel="ACMG-SF",
        quarantine="cleared",
        family_limit="10",
        id_release_policy="open",
    )

    assert _donor_draft(row)["code"] == "CAT-1"
    assert "http" not in "".join(_donor_draft(row).values())


def test_donor_from_draft_normalizes_unsafe_or_unknown_values():
    donor = _donor_from_draft(7, _draft(blood_type="Z", rh="maybe", cmv="nope"), 4, "https://example.com/c")

    assert donor.bank_user_id == 7
    assert donor.blood_type == ""
    assert donor.rh == ""
    assert donor.cmv == "unknown"
    assert donor.photo_key == PHOTO_KEYS[4 % len(PHOTO_KEYS)]
    assert donor.catalog_source_url == "https://example.com/c"
    assert donor.catalog_confirmed is True

    clamped = _donor_from_draft(7, _draft(code="", family_limit="99", quarantine="nope"), 0, "bundled-sample")
    assert clamped.code == "CAT-1"
    assert clamped.family_limit == 25
    assert clamped.quarantine == "cleared"
    assert clamped.catalog_source_url == ""
    assert _donor_from_draft(7, _draft(family_limit="nope"), 0, "").family_limit == 1
