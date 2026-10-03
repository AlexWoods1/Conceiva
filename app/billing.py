"""One-time Stripe Checkout that unlocks donor matching for a couple."""

from __future__ import annotations

from datetime import datetime, timezone

import stripe
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import User


def _configure(settings: Settings) -> None:
    stripe.api_key = settings.stripe_secret_key


def create_checkout_session(settings: Settings, user: User) -> str:
    """Create a Checkout Session and return the hosted payment URL.

    Args:
        settings: App settings with Stripe keys and base URL.
        user: Couple account purchasing unlock.

    Returns:
        Stripe-hosted Checkout URL.

    Raises:
        RuntimeError: Stripe is not configured or the session has no URL.
    """
    if not settings.stripe_enabled:
        raise RuntimeError("Stripe is not configured.")
    _configure(settings)
    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=[{"price": settings.stripe_price_id, "quantity": 1}],
        success_url=(
            f"{settings.app_base_url}/billing/success"
            "?session_id={CHECKOUT_SESSION_ID}"
        ),
        cancel_url=f"{settings.app_base_url}/billing/cancel",
        customer_email=user.email,
        client_reference_id=str(user.id),
        metadata={"user_id": str(user.id)},
    )
    if not session.url:
        raise RuntimeError("Stripe Checkout Session did not return a URL.")
    return session.url


def mark_user_paid(
    db: Session, user: User, checkout_session_id: str | None = None
) -> None:
    """Record a one-time matching unlock on the user row.

    Args:
        db: Open session. Caller commits.
        user: Couple user to unlock.
        checkout_session_id: Stripe Checkout Session id, when known.
    """
    if user.paid_at is None:
        user.paid_at = datetime.now(timezone.utc)
    if checkout_session_id:
        user.stripe_checkout_session_id = checkout_session_id


def fulfill_checkout_session(db: Session, session: stripe.checkout.Session) -> User | None:
    """Unlock the couple user referenced by a completed Checkout Session.

    Args:
        db: Open session. Caller commits.
        session: Stripe Checkout Session object.

    Returns:
        The unlocked user, or None if the session does not map to a couple user.
    """
    raw_id = session.client_reference_id or (session.metadata or {}).get("user_id")
    if not raw_id:
        return None
    try:
        user_id = int(raw_id)
    except (TypeError, ValueError):
        return None
    user = db.get(User, user_id)
    if user is None or user.role != "couple":
        return None
    mark_user_paid(db, user, checkout_session_id=session.id)
    return user


def retrieve_and_fulfill(
    settings: Settings, db: Session, session_id: str
) -> User | None:
    """Load a Checkout Session from Stripe and unlock if payment completed.

    Args:
        settings: App settings.
        db: Open session. Caller commits.
        session_id: Checkout Session id from the success redirect.

    Returns:
        Unlocked user when payment_status is paid, otherwise None.
    """
    if not settings.stripe_enabled:
        return None
    _configure(settings)
    session = stripe.checkout.Session.retrieve(session_id)
    if session.payment_status != "paid":
        return None
    return fulfill_checkout_session(db, session)


def parse_webhook_event(
    settings: Settings, payload: bytes, signature: str
) -> stripe.Event:
    """Verify a Stripe webhook signature and parse the event.

    Args:
        settings: App settings with webhook secret.
        payload: Raw request body.
        signature: Stripe-Signature header value.

    Returns:
        Verified Stripe event.

    Raises:
        RuntimeError: Webhook secret is missing.
        stripe.SignatureVerificationError: Signature is invalid.
    """
    if not settings.stripe_webhook_secret:
        raise RuntimeError("STRIPE_WEBHOOK_SECRET is not set.")
    return stripe.Webhook.construct_event(
        payload, signature, settings.stripe_webhook_secret
    )


def handle_webhook_event(db: Session, event: stripe.Event) -> User | None:
    """Apply side effects for supported Stripe events.

    Args:
        db: Open session. Caller commits.
        event: Verified Stripe event.

    Returns:
        Unlocked user for checkout.session.completed, else None.
    """
    if event["type"] != "checkout.session.completed":
        return None
    session = event["data"]["object"]
    return fulfill_checkout_session(db, session)
