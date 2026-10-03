"""Tests for application startup and the request session."""

from datetime import datetime, timezone

from sqlalchemy import create_engine, func, select, text
from starlette.testclient import TestClient

from app.main import _appointments_slot_id_is_unique, _ensure_appointments_slot_reusable
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

    assert [user.email for user in users] == [
        "bank@demo.local",
        "counselor@demo.local",
        "couple@demo.local",
    ]
    bank, counselor, couple = users
    assert verify_password(DEMO_COUPLE_PASSWORD, couple.password_hash)
    assert verify_password(DEMO_BANK_PASSWORD, bank.password_hash)
    assert counselor.role == "counselor"
    assert couple.consent_at is None
    assert couple.paid_at is not None
    assert bank.paid_at is None

    db = app.state.session_factory()
    try:
        assert db.scalar(select(func.count()).select_from(User)) == 3
    finally:
        db.close()


def test_existing_empty_database_gets_idempotent_seed(make_app, tmp_path):
    path = tmp_path / "existing.db"
    path.touch()
    app = make_app(database_path=path, seed_on_empty=True)

    assert [user.email for user in _users(app)] == [
        "bank@demo.local",
        "counselor@demo.local",
        "couple@demo.local",
    ]


def test_seed_is_idempotent_when_demos_already_exist(make_app, tmp_path):
    from app.seed import seed_demo

    path = tmp_path / "twice.db"
    app = make_app(database_path=path, seed_on_empty=True)
    assert len(_users(app)) == 3
    session = app.state.session_factory()
    try:
        seed_demo(session)
        session.commit()
    finally:
        session.close()
    assert len(_users(app)) == 3


def test_appointments_slot_unique_is_dropped_on_startup(tmp_path):
    path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE availability_slots (id INTEGER PRIMARY KEY)"))
        conn.execute(text("""
                CREATE TABLE appointments (
                    id INTEGER PRIMARY KEY,
                    couple_user_id INTEGER NOT NULL,
                    counselor_user_id INTEGER NOT NULL,
                    slot_id INTEGER NOT NULL UNIQUE,
                    status VARCHAR(32) NOT NULL,
                    created_at DATETIME NOT NULL
                )
                """))
        conn.execute(text("INSERT INTO users (id) VALUES (1), (2)"))
        conn.execute(text("INSERT INTO availability_slots (id) VALUES (1)"))
        conn.execute(
            text("""
                INSERT INTO appointments
                (id, couple_user_id, counselor_user_id, slot_id, status, created_at)
                VALUES (1, 1, 2, 1, 'cancelled', :created)
                """),
            {"created": datetime.now(timezone.utc).isoformat()},
        )
    with engine.begin() as conn:
        assert _appointments_slot_id_is_unique(conn) is True
    _ensure_appointments_slot_reusable(engine)
    with engine.begin() as conn:
        assert _appointments_slot_id_is_unique(conn) is False
        conn.execute(
            text("""
                INSERT INTO appointments
                (id, couple_user_id, counselor_user_id, slot_id, status, created_at)
                VALUES (2, 1, 2, 1, 'booked', :created)
                """),
            {"created": datetime.now(timezone.utc).isoformat()},
        )
    engine.dispose()
