"""Public pages: privacy and contact."""

from sqlalchemy import select
from starlette.testclient import TestClient

from app.models import ContactMessage


def test_public_pages_render(make_app):
    app = make_app()
    with TestClient(app) as client:
        home = client.get("/")
        privacy = client.get("/privacy")
        contact = client.get("/contact")
        start = client.get("/start")

    assert home.status_code == 200
    assert "Sample start" not in home.text
    assert "Hard stop" in home.text
    assert privacy.status_code == 200
    assert "not a diagnosis" in privacy.text.lower()
    assert "stand-in" in privacy.text.lower()
    assert start.status_code == 404
    assert contact.status_code == 200
    assert "privacy@example.com" in contact.text


def test_contact_form_saves_a_message(api):
    api.get("/contact")
    saved = api.post(
        "/contact",
        data={
            "name": "Alex",
            "email": "alex@example.com",
            "message": "Question about the demo records.",
        },
    )
    assert saved.status_code == 303
    assert saved.headers["location"] == "/contact"

    page = api.get("/contact")
    assert "not emailed" in page.json()["flash"]

    db = api.session()
    try:
        row = db.scalars(select(ContactMessage)).one()
        assert row.email == "alex@example.com"
        assert "demo records" in row.message
    finally:
        db.close()


def test_contact_form_rejects_a_short_message(api):
    api.get("/contact")
    response = api.post(
        "/contact",
        data={"name": "Alex", "email": "alex@example.com", "message": "too short"},
    )
    assert response.status_code == 400
    assert "10 and 2000" in response.json()["error"]
