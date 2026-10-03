"""Tests for password hashing and free-text redaction."""

from app.security import hash_password, redact_free_text, verify_password


def test_hash_password_verifies_and_uses_a_fresh_salt():
    first = hash_password("correct horse")
    second = hash_password("correct horse")

    assert first != second
    assert verify_password("correct horse", first)
    assert verify_password("correct horse", second)
    assert verify_password("wrong", first) is False


def test_verify_password_rejects_malformed_hashes():
    assert verify_password("secret", "not-a-hash") is False
    assert verify_password("secret", "zzzz$abcd") is False
    assert verify_password("secret", "aa$zz") is False


def test_redact_free_text_strips_contact_details_and_whitespace():
    raw = "Email me at Nurse@Clinic.Example and call +1 (415) 555-0199 today."

    cleaned = redact_free_text(raw)

    assert "Nurse@Clinic.Example" not in cleaned
    assert "[redacted-email]" in cleaned
    assert "[redacted-phone]" in cleaned
    assert "555" not in cleaned
    assert cleaned.startswith("Email me at")
    assert cleaned.endswith("today.")
    assert redact_free_text("   ") == ""
    assert redact_free_text("") == ""


def test_redact_free_text_caps_length():
    cleaned = redact_free_text("hello world", limit=5)

    assert cleaned == "hello..."
    assert redact_free_text("hello", limit=5) == "hello"
