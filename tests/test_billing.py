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
        api.client.post("/webhooks/stripe", content=b"{}", headers={"stripe-signature": "x"})
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
