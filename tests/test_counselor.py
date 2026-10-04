"""HTTP tests for shortlist, booking, counselor slots, and candidate reports."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import (
    Appointment,
    AppointmentDonor,
    AvailabilitySlot,
    CandidateReport,
    Carrier,
    CounselorProfile,
    Donor,
    ShortlistItem,
    User,
)
from app.seed import (
    DEMO_COUNSELOR_EMAIL,
    DEMO_COUNSELOR_PASSWORD,
    seed_demo,
)
from app.security import hash_password
from tests.test_http import _consent


def _seed(api):
    db = api.session()
    try:
        if db.scalars(select(User).where(User.email == DEMO_COUNSELOR_EMAIL)).first():
            return
        seed_demo(db)
        db.commit()
    finally:
        db.close()


def _login(api, email: str, password: str):
    api.get("/login")
    signed = api.post("/login", {"email": email, "password": password})
    assert signed.status_code == 303
    return signed


def _prepare_couple_with_shortlist(api, email: str = "person@example.com"):
    """Consent a couple, seed donors, complete the survey path, shortlist two donors."""
    _seed(api)
    _consent(api, email)
    db = api.session()
    try:
        couple = db.scalars(select(User).where(User.email == email)).one()
        donors = {
            donor.code: donor
            for donor in db.scalars(select(Donor).order_by(Donor.code))
        }
        dn100 = donors["DN-100"]
        dn240 = donors["DN-240"]
        db.add(
            Carrier(
                subject_type="couple",
                subject_id=couple.id,
                gene="CFTR",
                zygosity="heterozygous",
                condition="Cystic fibrosis",
            )
        )
        db.commit()
    finally:
        db.close()

    api.post(
        "/couple/history",
        {
            "blood_type": "O",
            "rh": "negative",
            "prior_pregnancies": "1",
            "prior_donors": "",
            "miscarriages": "0",
            "known_conditions": "",
            "adult_photo_key": "",
        },
    )
    api.post(
        "/couple/survey/clinical",
        {
            "ancestry": "Finnish",
            "cmv_requirement": "any",
            "clinical_notes": "notes for counselor only",
        },
    )
    api.post(
        "/couple/survey/preferences",
        {"id_release": "open", "family_limit": "2"},
    )
    assert api.post(f"/couple/shortlist/{dn100.id}/add").status_code == 303
    assert api.post(f"/couple/shortlist/{dn240.id}/add").status_code == 303
    return dn100, dn240


def _login_demo_counselor(api):
    _login(api, DEMO_COUNSELOR_EMAIL, DEMO_COUNSELOR_PASSWORD)
    if api.get("/counselor").headers.get("location") == "/consent":
        consented = api.post("/consent", {"agree": "yes"})
        assert consented.headers["location"] == "/counselor"


def test_shortlist_add_remove_and_isolation(api):
    dn100, dn240 = _prepare_couple_with_shortlist(api)
    listed = api.get("/couple/shortlist").json()
    assert {row["code"] for row in listed["rows"]} == {"DN-100", "DN-240"}
    assert listed["rows"][1]["hard_stop"] is True

    api.post(f"/couple/shortlist/{dn240.id}/remove")
    listed = api.get("/couple/shortlist").json()
    assert [row["code"] for row in listed["rows"]] == ["DN-100"]

    api.post("/logout")
    _consent(api, "other@example.com")
    other_list = api.get("/couple/shortlist").json()
    assert other_list["rows"] == []


def test_shortlist_rejects_over_cap(api, monkeypatch):
    monkeypatch.setattr("app.services.MAX_SHORTLIST", 2)
    monkeypatch.setattr("app.routes.MAX_SHORTLIST", 2)
    _seed(api)
    _consent(api, "person@example.com")
    db = api.session()
    try:
        donors = list(db.scalars(select(Donor).order_by(Donor.code)))
        donor_ids = [donor.id for donor in donors[:3]]
    finally:
        db.close()
    for donor_id in donor_ids[:2]:
        assert api.post(f"/couple/shortlist/{donor_id}/add").status_code == 303
    blocked = api.post(f"/couple/shortlist/{donor_ids[2]}/add")
    assert blocked.status_code == 303
    flash = api.get("/match").json()["flash"]
    assert "limited to 2" in flash
    db = api.session()
    try:
        couple = db.scalars(
            select(User).where(User.email == "person@example.com")
        ).one()
        count = len(
            list(
                db.scalars(
                    select(ShortlistItem).where(
                        ShortlistItem.couple_user_id == couple.id
                    )
                )
            )
        )
        assert count == 2
    finally:
        db.close()


def test_book_page_lists_every_counselor_with_open_slots(api):
    _seed(api)
    db = api.session()
    try:
        password_hash = hash_password("password1")
        second = User(
            email="second@demo.local",
            password_hash=password_hash,
            role="counselor",
            consent_at=datetime.now(timezone.utc),
        )
        db.add(second)
        db.flush()
        db.add(CounselorProfile(user_id=second.id, display_name="Second Counselor"))
        db.add(
            AvailabilitySlot(
                counselor_user_id=second.id,
                starts_at=datetime.now(timezone.utc).replace(tzinfo=None)
                + timedelta(days=4),
                duration_minutes=45,
            )
        )
        empty = User(
            email="empty@demo.local",
            password_hash=password_hash,
            role="counselor",
            consent_at=datetime.now(timezone.utc),
        )
        db.add(empty)
        db.flush()
        db.add(CounselorProfile(user_id=empty.id, display_name="No Slots"))
        db.commit()
    finally:
        db.close()

    _consent(api, "person@example.com")
    page = api.get("/couple/book").json()
    assert DEMO_COUNSELOR_EMAIL in page["counselor_emails"]
    assert "second@demo.local" in page["counselor_emails"]
    assert "empty@demo.local" not in page["counselor_emails"]


def test_booking_rejects_empty_shortlist_and_taken_slot(api):
    _seed(api)
    _consent(api, "emptybook@example.com")
    book_page = api.get("/couple/book").json()
    slot_id = book_page["slot_ids"][0]
    empty = api.post("/couple/book", {"slot_id": str(slot_id)})
    assert empty.status_code == 303
    assert "shortlist" in api.get("/couple/book").json()["flash"].lower()

    api.post("/logout")
    dn100, _dn240 = _prepare_couple_with_shortlist(api, email="booker@example.com")
    book_page = api.get("/couple/book").json()
    slot_id = book_page["slot_ids"][0]
    first = api.post("/couple/book", {"slot_id": str(slot_id)})
    assert first.status_code == 303
    assert first.headers["location"].startswith("/couple/appointments/")

    again = api.post("/couple/book", {"slot_id": str(slot_id)})
    assert again.status_code == 303
    assert "no longer available" in api.get("/couple/book").json()["flash"].lower()


def test_snapshot_freezes_ids_and_report_uses_live_profile(api):
    dn100, dn240 = _prepare_couple_with_shortlist(api)
    slot_id = api.get("/couple/book").json()["slot_ids"][0]
    booked = api.post("/couple/book", {"slot_id": str(slot_id)})
    appointment_id = int(booked.headers["location"].rsplit("/", 1)[-1])

    api.post(f"/couple/shortlist/{dn240.id}/remove")
    db = api.session()
    try:
        visit_rows = list(
            db.scalars(
                select(AppointmentDonor).where(
                    AppointmentDonor.appointment_id == appointment_id
                )
            )
        )
        assert {row.donor_id for row in visit_rows} == {dn100.id, dn240.id}
        assert {row.donor_code for row in visit_rows} == {"DN-100", "DN-240"}
        donor = db.get(Donor, dn100.id)
        donor.panel = ""
        db.commit()
    finally:
        db.close()

    api.post("/logout")
    _login_demo_counselor(api)
    report = api.post(f"/counselor/appointments/{appointment_id}/report")
    assert report.status_code == 303
    session = api.get(f"/counselor/appointments/{appointment_id}").json()
    assert set(session["candidate_codes"]) == {"DN-100", "DN-240"}
    by_code = dict(zip(session["candidate_codes"], session["report_texts"]))
    assert any("CFTR" in text for text in by_code["DN-240"])
    assert any("donor.panel" in text for text in by_code["DN-100"])


def test_visit_rebinds_donors_by_code_when_ids_go_stale(api):
    from sqlalchemy import text

    from app.services import resolve_visit_donors

    dn100, dn240 = _prepare_couple_with_shortlist(api)
    slot_id = api.get("/couple/book").json()["slot_ids"][0]
    booked = api.post("/couple/book", {"slot_id": str(slot_id)})
    appointment_id = int(booked.headers["location"].rsplit("/", 1)[-1])

    db = api.session()
    try:
        # * Simulate a /tmp reshuffle where codes stayed and primary keys moved.
        db.execute(text("PRAGMA foreign_keys=OFF"))
        stale = 99001
        for row in db.scalars(
            select(AppointmentDonor).where(
                AppointmentDonor.appointment_id == appointment_id
            )
        ):
            row.donor_id = stale
            stale += 1
        db.commit()
        donors = resolve_visit_donors(db, appointment_id)
        assert {donor.code for donor in donors} == {"DN-100", "DN-240"}
        assert {donor.id for donor in donors} == {dn100.id, dn240.id}
        db.commit()
    finally:
        db.close()

    api.post("/logout")
    _login_demo_counselor(api)
    session = api.get(f"/counselor/appointments/{appointment_id}").json()
    assert set(session["candidate_codes"]) == {"DN-100", "DN-240"}


def test_couple_cannot_run_counselor_report_routes(api):
    _prepare_couple_with_shortlist(api)
    slot_id = api.get("/couple/book").json()["slot_ids"][0]
    booked = api.post("/couple/book", {"slot_id": str(slot_id)})
    appointment_id = int(booked.headers["location"].rsplit("/", 1)[-1])
    denied = api.post(f"/counselor/appointments/{appointment_id}/report")
    assert denied.status_code == 303
    assert denied.headers["location"] == "/couple/history"


def test_counselor_cannot_open_another_counselors_appointment(api):
    _prepare_couple_with_shortlist(api)
    slot_id = api.get("/couple/book").json()["slot_ids"][0]
    booked = api.post("/couple/book", {"slot_id": str(slot_id)})
    appointment_id = int(booked.headers["location"].rsplit("/", 1)[-1])

    api.post("/logout")
    agreed = _consent(api, "other-counselor@example.com", role="counselor")
    assert agreed.headers["location"] == "/counselor"
    response = api.get(f"/counselor/appointments/{appointment_id}")
    assert response.status_code == 404


def test_counselor_can_add_and_cannot_delete_taken_slot(api):
    _seed(api)
    agreed = _consent(api, "fresh@example.com", role="counselor")
    assert agreed.headers["location"] == "/counselor"
    future = (datetime.now(timezone.utc) + timedelta(days=10)).strftime(
        "%Y-%m-%dT%H:%M"
    )
    created = api.post(
        "/counselor/slots",
        {"starts_at": future, "duration_minutes": "30"},
    )
    assert created.status_code == 303
    slots = api.get("/counselor/slots").json()
    assert True in slots["slot_open"]

    db = api.session()
    try:
        counselor = db.scalars(
            select(User).where(User.email == "fresh@example.com")
        ).one()
        slot = db.scalars(
            select(AvailabilitySlot)
            .where(AvailabilitySlot.counselor_user_id == counselor.id)
            .order_by(AvailabilitySlot.id.desc())
        ).first()
        couple = User(
            email="slot-booker@example.com",
            password_hash=hash_password("password1"),
            role="couple",
            consent_at=datetime.now(timezone.utc),
        )
        db.add(couple)
        db.flush()
        bank = db.scalars(select(User).where(User.role == "bank")).first()
        donor = Donor(
            bank_user_id=bank.id,
            code="DN-TEST",
            catalog_confirmed=True,
            blood_type="O",
            rh="negative",
            cmv="negative",
            quarantine="cleared",
            family_limit=10,
            id_release_policy="open",
            panel="ACMG-SF",
        )
        db.add(donor)
        db.flush()
        appointment = Appointment(
            couple_user_id=couple.id,
            counselor_user_id=counselor.id,
            slot_id=slot.id,
            status="booked",
            created_at=datetime.now(timezone.utc),
        )
        db.add(appointment)
        db.flush()
        db.add(AppointmentDonor(appointment_id=appointment.id, donor_id=donor.id))
        db.commit()
        slot_id = slot.id
    finally:
        db.close()

    blocked = api.post(f"/counselor/slots/{slot_id}/delete")
    assert blocked.status_code == 303
    assert "already has a booked visit" in api.get("/counselor/slots").json()["flash"]


def test_delete_couple_removes_shortlist_and_appointment(api):
    _prepare_couple_with_shortlist(api)
    slot_id = api.get("/couple/book").json()["slot_ids"][0]
    booked = api.post("/couple/book", {"slot_id": str(slot_id)})
    appointment_id = int(booked.headers["location"].rsplit("/", 1)[-1])
    db = api.session()
    try:
        couple_id = db.scalars(
            select(User.id).where(User.email == "person@example.com")
        ).one()
    finally:
        db.close()
    api.post("/account/delete", {"confirm": "yes"})

    db = api.session()
    try:
        assert (
            db.scalars(select(User).where(User.email == "person@example.com")).first()
            is None
        )
        assert db.get(Appointment, appointment_id) is None
        assert (
            list(
                db.scalars(
                    select(ShortlistItem).where(
                        ShortlistItem.couple_user_id == couple_id
                    )
                )
            )
            == []
        )
        assert list(db.scalars(select(CandidateReport))) == []
    finally:
        db.close()


def test_seed_includes_counselor_and_open_slots(api):
    _seed(api)
    db = api.session()
    try:
        counselor = db.scalars(
            select(User).where(User.email == DEMO_COUNSELOR_EMAIL)
        ).one()
        assert counselor.role == "counselor"
        slots = list(
            db.scalars(
                select(AvailabilitySlot).where(
                    AvailabilitySlot.counselor_user_id == counselor.id
                )
            )
        )
        assert len(slots) >= 3
        assert db.get(CounselorProfile, counselor.id).display_name
    finally:
        db.close()


def test_couple_can_cancel_appointment_and_frees_slot(api):
    _prepare_couple_with_shortlist(api)
    book_page = api.get("/couple/book").json()
    slot_id = book_page["slot_ids"][0]
    booked = api.post("/couple/book", {"slot_id": str(slot_id)})
    appointment_id = int(booked.headers["location"].rsplit("/", 1)[-1])

    after_book = api.get("/couple/book").json()
    assert slot_id not in after_book["slot_ids"]

    cancelled = api.post(f"/couple/appointments/{appointment_id}/cancel")
    assert cancelled.status_code == 303
    detail = api.get(f"/couple/appointments/{appointment_id}").json()
    assert detail["flash"] == "Visit cancelled. The slot is open again."

    db = api.session()
    try:
        appointment = db.get(Appointment, appointment_id)
        assert appointment.status == "cancelled"
    finally:
        db.close()

    reopen = api.get("/couple/book").json()
    assert slot_id in reopen["slot_ids"]

    again = api.post(f"/couple/appointments/{appointment_id}/cancel")
    assert again.status_code == 303
    assert (
        "already cancelled"
        in api.get(f"/couple/appointments/{appointment_id}").json()["flash"]
    )


def test_counselor_can_cancel_appointment(api):
    _prepare_couple_with_shortlist(api)
    slot_id = api.get("/couple/book").json()["slot_ids"][0]
    booked = api.post("/couple/book", {"slot_id": str(slot_id)})
    appointment_id = int(booked.headers["location"].rsplit("/", 1)[-1])

    api.post("/logout")
    _login_demo_counselor(api)
    cancelled = api.post(f"/counselor/appointments/{appointment_id}/cancel")
    assert cancelled.status_code == 303
    db = api.session()
    try:
        assert db.get(Appointment, appointment_id).status == "cancelled"
    finally:
        db.close()
    blocked = api.post(f"/counselor/appointments/{appointment_id}/report")
    assert blocked.status_code == 303
    assert (
        "Cancelled visits"
        in api.get(f"/counselor/appointments/{appointment_id}").json()["flash"]
    )
