"""Tests for the non-clinical resemblance stub."""

import hashlib

from app.phenotype import resemblance_score


def test_resemblance_score_is_absent_unless_both_keys_and_the_flag_are_set():
    assert resemblance_score("geo-1", "adult-1", False) is None
    assert resemblance_score("", "adult-1", True) is None
    assert resemblance_score("geo-1", "", True) is None


def test_resemblance_score_is_stable_and_bounded():
    expected = 35 + hashlib.sha256(b"geo-1:adult-1").digest()[0] % 46

    assert resemblance_score("geo-1", "adult-1", True) == expected
    assert resemblance_score("geo-1", "adult-1", True) == expected
    assert 35 <= expected <= 80
