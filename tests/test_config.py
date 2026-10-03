"""Tests for environment-backed settings."""

from pathlib import Path

import pytest

from app.config import DEV_SESSION_SECRET, PROJECT_ROOT, load_settings


def test_load_settings_uses_defaults_when_environment_is_empty(monkeypatch):
    monkeypatch.delenv("DATABASE_PATH", raising=False)
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("SEED_ON_EMPTY", raising=False)
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("CONTACT_EMAIL", raising=False)
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("STRIPE_PRICE_ID", raising=False)
    monkeypatch.delenv("APP_BASE_URL", raising=False)
    monkeypatch.delenv("MOTILITY_UPLOADS_ENABLED", raising=False)

    settings = load_settings()

    assert settings.database_path == PROJECT_ROOT / "data" / "app.db"
    assert settings.session_secret == DEV_SESSION_SECRET
    assert settings.llm_api_key == ""
    assert settings.llm_base_url == "https://api.openai.com/v1"
    assert settings.llm_model == "gpt-4o-mini"
    assert settings.seed_on_empty is True
    assert settings.secure_cookies is False
    assert settings.contact_email == "privacy@example.com"
    assert settings.stripe_secret_key == ""
    assert settings.stripe_price_id == ""
    assert settings.stripe_enabled is False
    assert settings.app_base_url == "http://127.0.0.1:8000"
    assert settings.motility_uploads_enabled is True


def test_load_settings_reads_environment_overrides(monkeypatch, tmp_path):
    database_path = tmp_path / "custom.db"
    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    monkeypatch.setenv("SESSION_SECRET", "s3cret")
    monkeypatch.setenv("LLM_API_KEY", "key")
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1/")
    monkeypatch.setenv("LLM_MODEL", "demo")
    monkeypatch.setenv("SEED_ON_EMPTY", "false")

    settings = load_settings()

    assert settings.database_path == database_path
    assert settings.session_secret == "s3cret"
    assert settings.llm_api_key == "key"
    assert settings.llm_base_url == "https://llm.example/v1"
    assert settings.llm_model == "demo"
    assert settings.seed_on_empty is False


def test_vercel_uses_temporary_storage_and_secure_cookies(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("SESSION_SECRET", "vercel-secret")
    monkeypatch.delenv("DATABASE_PATH", raising=False)
    monkeypatch.delenv("MOTILITY_UPLOADS_ENABLED", raising=False)
    monkeypatch.setenv("CONTACT_EMAIL", "desk@example.com")

    settings = load_settings()

    assert settings.database_path == Path("/tmp/donor-match.db")
    assert settings.secure_cookies is True
    assert settings.contact_email == "desk@example.com"
    assert settings.motility_uploads_enabled is True
    assert settings.motility_demo_fallback is True
    assert settings.on_vercel is True


def test_vercel_rejects_default_session_secret(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        load_settings()


def test_blank_contact_email_falls_back(monkeypatch):
    monkeypatch.setenv("CONTACT_EMAIL", "  ")
    assert load_settings().contact_email == "privacy@example.com"


def test_stripe_enabled_requires_secret_and_price(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test")
    monkeypatch.delenv("STRIPE_PRICE_ID", raising=False)
    assert load_settings().stripe_enabled is False
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_123")
    assert load_settings().stripe_enabled is True


def test_seed_flag_accepts_common_true_strings(monkeypatch):
    monkeypatch.setenv("SEED_ON_EMPTY", "YES")
    assert load_settings().seed_on_empty is True
    monkeypatch.setenv("SEED_ON_EMPTY", "1")
    assert load_settings().seed_on_empty is True
    monkeypatch.setenv("SEED_ON_EMPTY", "no")
    assert load_settings().seed_on_empty is False
