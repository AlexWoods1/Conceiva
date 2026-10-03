"""Password hashing and free-text redaction."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets

_EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.IGNORECASE)
_PHONE_RE = re.compile(r"\b(?:\+?\d[\d\s().\-]{7,}\d)\b")
_PBKDF2_ROUNDS = 120_000


def hash_password(password: str) -> str:
    """Hash a password with PBKDF2-HMAC-SHA256.

    Args:
        password: Plain text from the registration form.

    Returns:
        Salt and digest joined by ``$``.
    """
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS
    )
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Check a password against a stored hash.

    Args:
        password: Plain text from the login form.
        stored: Value produced by hash_password.

    Returns:
        True when the password matches.
    """
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS
    )
    return hmac.compare_digest(digest.hex(), digest_hex)


def redact_free_text(value: str, limit: int = 200) -> str:
    """Strip contact identifiers and cap length before model input.

    Args:
        value: User-entered history or notes.
        limit: Maximum characters kept.

    Returns:
        A single-line redacted string. Empty when the input is empty.
    """
    cleaned = _EMAIL_RE.sub("[redacted-email]", value or "")
    cleaned = _PHONE_RE.sub("[redacted-phone]", cleaned)
    cleaned = " ".join(cleaned.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "..."
