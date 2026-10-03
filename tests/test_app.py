"""Tests for application startup and the request session."""

from sqlalchemy import func, select
from starlette.testclient import TestClient

from app.models import User
from app.security import verify_password
from app.seed import DEMO_BANK_PASSWORD, DEMO_COUPLE_PASSWORD


def _users(app):
    db = app.state.session_factory()
    try:
        return list(db.scalars(select(User).order_by(User.email)))
    finally:
        db.close()


def test_health(make_app):
    app = make_app()
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert _users(app) == []


def test_empty_database_is_seeded_once(make_app, tmp_path):
    app = make_app(database_path=tmp_path / "seeded.db", seed_on_empty=True)
    users = _users(app)

    assert [user.email for user in users] == ["bank@demo.local", "couple@demo.local"]
    couple, bank = users[1], users[0]
    assert verify_password(DEMO_COUPLE_PASSWORD, couple.password_hash)
    assert verify_password(DEMO_BANK_PASSWORD, bank.password_hash)
    assert couple.consent_at is None

    db = app.state.session_factory()
    try:
        assert db.scalar(select(func.count()).select_from(User)) == 2
    finally:
        db.close()


def test_existing_database_file_is_not_seeded(make_app, tmp_path):
    path = tmp_path / "existing.db"
    path.touch()
    app = make_app(database_path=path, seed_on_empty=True)

    assert _users(app) == []
