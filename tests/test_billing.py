"""HTTP tests for the one-time Stripe couple match unlock."""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import select
from starlette.testclient import TestClient

from app.billing import mark_user_paid
from app.models import User
from tests.conftest import Api
from tests.test_http import _consent, _user

COUPLE = "couple@example.com"


def _stripe_api(make_app, render_stub) -> Api:
    app = make_app(
        stripe_secret_key="sk_test_x",
        stripe_webhook_secret="whsec_test",
        stripe_price_id="price_test",
        app_base_url="http://testserver",
    )
    client = TestClient(app, follow_redirects=False)
    return Api(client, app)


def test_unpaid_couple_is_redirected_to_unlock(make_app, render_stub):
    api = _stripe_api(make_app, render_stub)
    with api.client:
        _consent(api, COUPLE)
        for path in ("/match", "/match/1"):
            response = api.get(path)
            assert response.status_code == 303
            assert response.headers["location"] == "/billing/unlock"

        unlock = api.get("/billing/unlock")
        assert unlock.status_code == 200
        assert unlock.json()["template"] == "billing_unlock.html"


def test_unpaid_couple_can_still_fill_intake(make_app, render_stub):
    api = _stripe_api(make_app, render_stub)
    with api.client:
        _consent(api, COUPLE)
        for path in ("/couple/history", "/couple/carriers", "/couple/survey/clinical"):
            assert api.get(path).status_code == 200


def test_bank_is_free_when_stripe_is_on(make_app, render_stub):
    api = _stripe_api(make_app, render_stub)
    with api.client:
        _consent(api, "bank@example.com", role="bank")
        page = api.get("/bank")
        assert page.status_code == 200
        assert page.json()["template"] == "bank.html"
        assert api.get("/billing/unlock").headers["location"] == "/bank"


def test_checkout_redirects_to_stripe_url(make_app, render_stub, monkeypatch):
    api = _stripe_api(make_app, render_stub)

    def fake_create(settings, user):
        assert user.email == COUPLE
        assert settings.stripe_price_id == "price_test"
        return "https://checkout.stripe.test/session"

    monkeypatch.setattr("app.routes.create_checkout_session", fake_create)
    with api.client:
        _consent(api, COUPLE)
        api.get("/billing/unlock")
        started = api.post("/billing/checkout")
        assert started.status_code == 303
        assert started.headers["location"] == "https://checkout.stripe.test/session"


def test_webhook_marks_couple_paid(make_app, render_stub, monkeypatch):
    api = _stripe_api(make_app, render_stub)
    with api.client:
        _consent(api, COUPLE)
        user = _user(api, COUPLE)
        assert user.paid_at is None

        session = SimpleNamespace(
            id="cs_test_1",
            client_reference_id=str(user.id),
            metadata={"user_id": str(user.id)},
            payment_status="paid",
        )
        event = {"type": "checkout.session.completed", "data": {"object": session}}
        monkeypatch.setattr(
            "app.routes.parse_webhook_event",
            lambda settings, payload, signature: event,
        )

        response = api.client.post(
            "/webhooks/stripe",
            content=b"{}",
            headers={"stripe-signature": "t=1,v1=x"},
        )
        assert response.status_code == 200
        assert response.json() == {"received": True}

        paid = _user(api, COUPLE)
        assert paid.paid_at is not None
        assert paid.stripe_checkout_session_id == "cs_test_1"

        matches = api.get("/match")
        assert matches.status_code == 200
        assert matches.json()["template"] == "match_list.html"


def test_webhook_ignores_bank_accounts(make_app, render_stub, monkeypatch):
    api = _stripe_api(make_app, render_stub)
    with api.client:
        _consent(api, "bank@example.com", role="bank")
        bank = _user(api, "bank@example.com")
        session = SimpleNamespace(
            id="cs_test_bank",
            client_reference_id=str(bank.id),
            metadata={"user_id": str(bank.id)},
            payment_status="paid",
        )
        event = {"type": "checkout.session.completed", "data": {"object": session}}
        monkeypatch.setattr(
            "app.routes.parse_webhook_event",
            lambda settings, payload, signature: event,
        )
        api.client.post(
            "/webhooks/stripe", content=b"{}", headers={"stripe-signature": "x"}
        )
        assert _user(api, "bank@example.com").paid_at is None


def test_success_fulfillment_unlocks_when_webhook_lags(
    make_app, render_stub, monkeypatch
):
    api = _stripe_api(make_app, render_stub)

    def fake_retrieve(settings, db, session_id):
        assert session_id == "cs_test_lag"
        user = db.scalars(select(User).where(User.email == COUPLE)).one()
        mark_user_paid(db, user, checkout_session_id=session_id)
        return user

    monkeypatch.setattr("app.routes.retrieve_and_fulfill", fake_retrieve)
    with api.client:
        _consent(api, COUPLE)
        done = api.get("/billing/success?session_id=cs_test_lag")
        assert done.status_code == 303
        assert done.headers["location"] == "/match"
        assert _user(api, COUPLE).paid_at is not None


def test_stripe_disabled_leaves_matches_open(api):
    _consent(api, COUPLE)
    page = api.get("/match")
    assert page.status_code == 200
    assert page.json()["template"] == "match_list.html"


def _settings(**overrides):
    from pathlib import Path

    from app.config import Settings

    values = {
        "database_path": Path("unused.db"),
        "session_secret": "test",
        "llm_api_key": "",
        "llm_base_url": "https://llm.example/v1",
        "llm_model": "test-model",
        "enable_face_compare": False,
        "seed_on_empty": False,
        "stripe_secret_key": "sk_test_x",
        "stripe_webhook_secret": "whsec_test",
        "stripe_price_id": "price_test",
        "app_base_url": "http://testserver",
    }
    values.update(overrides)
    return Settings(**values)


def _db_user(db, email: str, role: str = "couple") -> User:
    from app.security import hash_password

    user = User(email=email, password_hash=hash_password("password1"), role=role)
    db.add(user)
    db.commit()
    return user


def test_mark_user_paid_is_idempotent(db):
    from app.billing import mark_user_paid

    user = _db_user(db, "couple@example.com")
    mark_user_paid(db, user, checkout_session_id="cs_first")
    db.commit()
    first_paid_at = user.paid_at
    assert first_paid_at is not None

    mark_user_paid(db, user, checkout_session_id="cs_second")
    db.commit()
    assert user.paid_at == first_paid_at
    assert user.stripe_checkout_session_id == "cs_second"


def test_fulfill_checkout_session_resolves_metadata_and_rejects_bad_targets(db):
    from app.billing import fulfill_checkout_session

    couple = _db_user(db, "couple@example.com")
    bank = _db_user(db, "bank@example.com", role="bank")

    assert (
        fulfill_checkout_session(
            db, SimpleNamespace(id="cs_a", client_reference_id=None, metadata={})
        )
        is None
    )
    assert (
        fulfill_checkout_session(
            db, SimpleNamespace(id="cs_b", client_reference_id="not-int", metadata={})
        )
        is None
    )
    assert (
        fulfill_checkout_session(
            db,
            SimpleNamespace(id="cs_c", client_reference_id="99999", metadata={}),
        )
        is None
    )
    assert (
        fulfill_checkout_session(
            db,
            SimpleNamespace(
                id="cs_d",
                client_reference_id=str(bank.id),
                metadata={"user_id": str(bank.id)},
            ),
        )
        is None
    )

    unlocked = fulfill_checkout_session(
        db,
        SimpleNamespace(
            id="cs_meta",
            client_reference_id=None,
            metadata={"user_id": str(couple.id)},
        ),
    )
    db.commit()
    assert unlocked is not None
    assert unlocked.id == couple.id
    assert unlocked.paid_at is not None
    assert unlocked.stripe_checkout_session_id == "cs_meta"


def test_handle_webhook_event_ignores_other_types(db):
    from app.billing import handle_webhook_event

    user = _db_user(db, "couple@example.com")
    event = {
        "type": "invoice.paid",
        "data": {
            "object": SimpleNamespace(
                id="cs_invoice",
                client_reference_id=str(user.id),
                metadata={},
            )
        },
    }

    assert handle_webhook_event(db, event) is None
    db.refresh(user)
    assert user.paid_at is None


def test_create_checkout_session_requires_stripe_enabled():
    from app.billing import create_checkout_session

    user = User(email="couple@example.com", password_hash="x", role="couple")
    try:
        create_checkout_session(
            _settings(stripe_secret_key="", stripe_price_id=""), user
        )
    except RuntimeError as exc:
        assert "Stripe is not configured" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_retrieve_and_fulfill_requires_paid_status(db, monkeypatch):
    from app import billing

    couple = _db_user(db, "couple@example.com")
    settings = _settings()

    monkeypatch.setattr(
        billing.stripe.checkout.Session,
        "retrieve",
        lambda _session_id: SimpleNamespace(
            id="cs_unpaid",
            payment_status="unpaid",
            client_reference_id=str(couple.id),
            metadata={},
        ),
    )
    assert billing.retrieve_and_fulfill(settings, db, "cs_unpaid") is None
    db.refresh(couple)
    assert couple.paid_at is None

    monkeypatch.setattr(
        billing.stripe.checkout.Session,
        "retrieve",
        lambda _session_id: SimpleNamespace(
            id="cs_paid",
            payment_status="paid",
            client_reference_id=str(couple.id),
            metadata={},
        ),
    )
    unlocked = billing.retrieve_and_fulfill(settings, db, "cs_paid")
    db.commit()
    assert unlocked is not None
    assert unlocked.paid_at is not None


def test_parse_webhook_event_requires_secret():
    from app.billing import parse_webhook_event

    try:
        parse_webhook_event(_settings(stripe_webhook_secret=""), b"{}", "sig")
    except RuntimeError as exc:
        assert "STRIPE_WEBHOOK_SECRET" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
