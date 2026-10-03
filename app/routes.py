"""HTTP routes for the couple and sperm-bank site."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants import (
    ADULT_PHOTO_KEYS,
    BLOOD_TYPES,
    COMMON_CARRIERS,
    CMV_REQUIREMENT_LABELS,
    CMV_REQUIREMENTS,
    CMV_STATUS,
    CMV_STATUS_LABELS,
    ID_RELEASE,
    ID_RELEASE_LABELS,
    PHOTO_KEYS,
    QUARANTINE,
    QUARANTINE_LABELS,
    RH_LABELS,
    RH_VALUES,
    ROLES,
    ZYGOSITIES,
    ZYGOSITY_LABELS,
)
from app.llm import explain
from app.models import Carrier, ContactMessage, Donor, User
from app.scrape import CatalogDonor, fetch_catalog_html, parse_catalog
from app.security import hash_password, verify_password
from app.seed import (
    DEMO_BANK_EMAIL,
    DEMO_BANK_PASSWORD,
    DEMO_BLURBS,
    DEMO_COUPLE_EMAIL,
    DEMO_COUPLE_PASSWORD,
)
from app.services import (
    delete_account,
    find_ranked_donor,
    get_or_create_history,
    get_or_create_profile,
    get_or_create_survey,
    latest_explanation,
    list_carriers,
    packet_for,
    ranked_matches,
    store_explanation,
)

router = APIRouter()
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
TEMPLATES.env.globals.update(
    rh_labels=RH_LABELS,
    cmv_requirement_labels=CMV_REQUIREMENT_LABELS,
    cmv_status_labels=CMV_STATUS_LABELS,
    id_release_labels=ID_RELEASE_LABELS,
    quarantine_labels=QUARANTINE_LABELS,
    demo_blurbs=DEMO_BLURBS,
    zygosity_labels=ZYGOSITY_LABELS,
    common_carriers=COMMON_CARRIERS,
)
FIXTURE_CATALOG = Path(__file__).resolve().parent / "fixtures" / "sample_catalog.html"


def _csrf(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


def _render(request: Request, name: str, user: User | None, status_code: int = 200, **extra):
    context = {
        "user": user,
        "csrf": _csrf(request),
        "flash": request.session.pop("flash", ""),
        "face_compare": request.app.state.settings.enable_face_compare,
        "demo_couple_email": DEMO_COUPLE_EMAIL,
        "demo_couple_password": DEMO_COUPLE_PASSWORD,
        "demo_bank_email": DEMO_BANK_EMAIL,
        "demo_bank_password": DEMO_BANK_PASSWORD,
        "contact_email": request.app.state.settings.contact_email,
        **extra,
    }
    return TEMPLATES.TemplateResponse(request, name, context, status_code=status_code)


def _flash(request: Request, message: str) -> None:
    request.session["flash"] = message


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


async def _form(request: Request):
    form = await request.form()
    expected = request.session.get("csrf")
    if not expected or form.get("csrf") != expected:
        raise HTTPException(status_code=400, detail="CSRF check failed.")
    return form


def _db(request: Request) -> Session:
    return request.state.db


def _user(request: Request) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return _db(request).get(User, user_id)


def _require_user(request: Request) -> User:
    user = _user(request)
    if user is None:
        raise _RedirectNeeded("/login")
    return user


def _require_consent(request: Request, role: str) -> User:
    user = _require_user(request)
    if user.role != role:
        raise _RedirectNeeded("/bank" if user.role == "bank" else "/couple/history")
    if user.consent_at is None:
        raise _RedirectNeeded("/consent")
    return user


class _RedirectNeeded(Exception):
    """Internal redirect used by route guards."""

    def __init__(self, path: str) -> None:
        self.path = path


def _choice(value: str, options: tuple[str, ...], label: str) -> str:
    if value not in options:
        raise ValueError(f"Choose a valid {label}.")
    return value


def _bounded_int(value: str, label: str, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a whole number.") from exc
    if number < low or number > high:
        raise ValueError(f"{label} must be between {low} and {high}.")
    return number


def _gene(value: str) -> str:
    gene = value.strip().upper()
    if not gene or len(gene) > 32 or not gene.replace("-", "").isalnum():
        raise ValueError(
            "Enter the gene code from the lab report, such as CFTR. Use letters, numbers, or hyphens."
        )
    return gene


def _carrier_fields(form) -> tuple[str, str, str]:
    """Read a carrier row from the form.

    A common-report choice supplies both the condition and its gene code.
    "Other" uses the typed condition and gene code.

    Args:
        form: Posted form fields.

    Returns:
        Gene code, zygosity, and condition name.

    Raises:
        ValueError: A required field is missing or not an allowed choice.
    """
    zygosity = _choice(str(form.get("zygosity", "")), ZYGOSITIES, "copy count")
    example = str(form.get("example", "")).strip().upper()
    known = {gene: condition for gene, condition in COMMON_CARRIERS}
    if example and example != "OTHER":
        if example not in known:
            raise ValueError("Choose a condition from the list, or choose Other.")
        return example, zygosity, known[example]
    condition = _clean_text(str(form.get("condition", "")), 255)
    if not condition:
        raise ValueError("Enter the condition name from the report, such as cystic fibrosis.")
    return _gene(str(form.get("gene", ""))), zygosity, condition


def _clean_text(value: str, limit: int) -> str:
    return " ".join(value.split())[:limit]


def _message_text(value: str) -> str:
    lines = [" ".join(line.split()) for line in value.splitlines()]
    text = "\n".join(line for line in lines if line).strip()
    if len(text) < 10 or len(text) > 2000:
        raise ValueError("Write a message between 10 and 2000 characters.")
    return text


def _email_address(value: str) -> str:
    email = value.strip().lower()
    if "@" not in email or "." not in email.split("@")[-1] or len(email) > 255:
        raise ValueError("Enter an email address.")
    return email


@router.get("/")
def home(request: Request):
    """Marketing home with a couple door and a bank door."""
    return _render(request, "home.html", _user(request))


@router.get("/start")
def sample_start(request: Request):
    """Walk through the seeded couple and bank demos."""
    return _render(request, "start.html", _user(request))


@router.get("/privacy")
def privacy(request: Request):
    """Privacy policy for the demo."""
    return _render(request, "privacy.html", _user(request))


@router.get("/contact")
def contact_form(request: Request):
    """Contact page."""
    return _render(request, "contact.html", _user(request), form={})


@router.post("/contact")
async def contact_submit(request: Request):
    """Store a contact message. This demo does not send email."""
    form = await _form(request)
    payload = {
        "name": str(form.get("name", "")),
        "email": str(form.get("email", "")),
        "message": str(form.get("message", "")),
    }
    try:
        name = _clean_text(payload["name"], 80)
        if len(name) < 2:
            raise ValueError("Enter your name.")
        email = _email_address(payload["email"])
        message = _message_text(payload["message"])
    except ValueError as exc:
        return _render(
            request,
            "contact.html",
            _user(request),
            status_code=400,
            error=str(exc),
            form=payload,
        )
    _db(request).add(
        ContactMessage(
            name=name,
            email=email,
            message=message,
            created_at=datetime.now(timezone.utc),
        )
    )
    _db(request).commit()
    _flash(request, "Message saved in this demo. It is not emailed.")
    return _redirect("/contact")


@router.get("/health")
def health() -> dict[str, str]:
    """Load balancer health check."""
    return {"status": "ok"}


@router.get("/login")
def login_form(request: Request, role: str = "couple"):
    """Sign-in and registration form."""
    if role not in ROLES:
        role = "couple"
    return _render(request, "login.html", _user(request), role=role)


@router.post("/login")
async def login_submit(request: Request):
    """Sign in an existing couple or bank account."""
    form = await _form(request)
    email = str(form.get("email", "")).strip().lower()
    password = str(form.get("password", ""))
    user = _db(request).scalars(select(User).where(User.email == email)).first()
    if user is None or not verify_password(password, user.password_hash):
        return _render(
            request,
            "login.html",
            None,
            status_code=400,
            role="couple",
            error="Email or password does not match a record.",
        )
    request.session["user_id"] = user.id
    if user.consent_at is None:
        return _redirect("/consent")
    return _redirect("/bank" if user.role == "bank" else "/couple/history")


@router.post("/register")
async def register_submit(request: Request):
    """Create one couple login or one enterprise bank login."""
    form = await _form(request)
    email = str(form.get("email", "")).strip().lower()
    password = str(form.get("password", ""))
    role = str(form.get("role", ""))
    error = ""
    if role not in ROLES:
        error = "Choose couple or sperm bank."
    elif "@" not in email or len(email) > 255:
        error = "Enter an email address."
    elif len(password) < 8:
        error = "Use a password of at least 8 characters."
    elif _db(request).scalars(select(User).where(User.email == email)).first():
        error = "That email is already registered."
    if error:
        shown_role = role if role in ROLES else "couple"
        return _render(request, "login.html", None, status_code=400, role=shown_role, error=error)
    user = User(email=email, password_hash=hash_password(password), role=role)
    _db(request).add(user)
    _db(request).commit()
    _db(request).refresh(user)
    request.session["user_id"] = user.id
    return _redirect("/consent")


@router.post("/logout")
async def logout(request: Request):
    """End the session."""
    await _form(request)
    request.session.clear()
    return _redirect("/")


@router.get("/consent")
def consent_form(request: Request):
    """Consent gate shown before genetic fields or scores."""
    try:
        user = _require_user(request)
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    return _render(request, "consent.html", user)


@router.post("/consent")
async def consent_submit(request: Request):
    """Record consent."""
    form = await _form(request)
    try:
        user = _require_user(request)
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    if form.get("agree") != "yes":
        return _render(
            request,
            "consent.html",
            user,
            status_code=400,
            error="Consent is required before genetic fields or a match score.",
        )
    user.consent_at = datetime.now(timezone.utc)
    _db(request).commit()
    return _redirect("/bank" if user.role == "bank" else "/couple/history")


@router.get("/account")
def account(request: Request):
    """Account close page."""
    try:
        user = _require_user(request)
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    return _render(request, "account.html", user)


@router.post("/account/delete")
async def account_delete(request: Request):
    """Delete the account and the records tied to it."""
    form = await _form(request)
    try:
        user = _require_user(request)
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    if form.get("confirm") != "yes":
        return _render(
            request,
            "account.html",
            user,
            status_code=400,
            error="Check the box to close the account.",
        )
    delete_account(_db(request), user)
    _db(request).commit()
    request.session.clear()
    return _redirect("/")


@router.get("/couple/history")
def history_form(request: Request):
    """Blood type and previous-history form."""
    try:
        user = _require_consent(request, "couple")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    db = _db(request)
    return _render(
        request,
        "history.html",
        user,
        profile=get_or_create_profile(db, user.id),
        history=get_or_create_history(db, user.id),
        blood_types=BLOOD_TYPES,
        rh_values=RH_VALUES,
        adult_photos=ADULT_PHOTO_KEYS,
    )


@router.post("/couple/history")
async def history_submit(request: Request):
    """Save blood type and previous history."""
    form = await _form(request)
    try:
        user = _require_consent(request, "couple")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    db = _db(request)
    profile = get_or_create_profile(db, user.id)
    history = get_or_create_history(db, user.id)
    try:
        profile.blood_type = _choice(str(form.get("blood_type", "")), BLOOD_TYPES, "blood type")
        profile.rh = _choice(str(form.get("rh", "")), RH_VALUES, "Rh status")
        history.prior_pregnancies = _bounded_int(str(form.get("prior_pregnancies", "0")), "Prior pregnancies", 0, 30)
        history.miscarriages = _bounded_int(str(form.get("miscarriages", "0")), "Miscarriages", 0, 30)
        history.prior_donors = _clean_text(str(form.get("prior_donors", "")), 500)
        history.known_conditions = _clean_text(str(form.get("known_conditions", "")), 500)
        if request.app.state.settings.enable_face_compare:
            adult = str(form.get("adult_photo_key", ""))
            profile.adult_photo_key = adult if adult in ADULT_PHOTO_KEYS else ""
    except ValueError as exc:
        return _render(
            request,
            "history.html",
            user,
            status_code=400,
            profile=profile,
            history=history,
            blood_types=BLOOD_TYPES,
            rh_values=RH_VALUES,
            adult_photos=ADULT_PHOTO_KEYS,
            error=str(exc),
        )
    db.commit()
    _flash(request, "History saved.")
    return _redirect("/couple/carriers")


@router.get("/couple/carriers")
def carriers_form(request: Request):
    """Couple carrier form."""
    try:
        user = _require_consent(request, "couple")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    return _render(
        request,
        "carriers.html",
        user,
        carriers=list_carriers(_db(request), "couple", user.id),
        zygosities=ZYGOSITIES,
        subject_label="your profile",
    )


@router.post("/couple/carriers")
async def carriers_submit(request: Request):
    """Add one carrier row for the couple."""
    form = await _form(request)
    try:
        user = _require_consent(request, "couple")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    db = _db(request)
    try:
        gene, zygosity, condition = _carrier_fields(form)
        db.add(
            Carrier(
                subject_type="couple",
                subject_id=user.id,
                gene=gene,
                zygosity=zygosity,
                condition=condition,
            )
        )
        db.commit()
    except ValueError as exc:
        return _render(
            request,
            "carriers.html",
            user,
            status_code=400,
            carriers=list_carriers(db, "couple", user.id),
            zygosities=ZYGOSITIES,
            subject_label="your profile",
            error=str(exc),
        )
    _flash(request, "Carrier result saved.")
    return _redirect("/couple/carriers")


@router.post("/couple/carriers/{carrier_id}/delete")
async def carriers_delete(request: Request, carrier_id: int):
    """Delete one of the couple's carrier rows."""
    await _form(request)
    try:
        user = _require_consent(request, "couple")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    db = _db(request)
    row = db.get(Carrier, carrier_id)
    if row is None or row.subject_type != "couple" or row.subject_id != user.id:
        raise HTTPException(status_code=404, detail="Carrier row not found.")
    db.delete(row)
    db.commit()
    _flash(request, "Carrier row removed.")
    return _redirect("/couple/carriers")


@router.get("/couple/survey/clinical")
def clinical_form(request: Request):
    """First survey step."""
    try:
        user = _require_consent(request, "couple")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    return _render(
        request,
        "survey_clinical.html",
        user,
        survey=get_or_create_survey(_db(request), user.id),
        cmv_requirements=CMV_REQUIREMENTS,
    )


@router.post("/couple/survey/clinical")
async def clinical_submit(request: Request):
    """Save the clinical survey step."""
    form = await _form(request)
    try:
        user = _require_consent(request, "couple")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    db = _db(request)
    survey = get_or_create_survey(db, user.id)
    try:
        survey.cmv_requirement = _choice(str(form.get("cmv_requirement", "")), CMV_REQUIREMENTS, "CMV requirement")
        survey.ancestry = _clean_text(str(form.get("ancestry", "")), 255)
        survey.clinical_notes = _clean_text(str(form.get("clinical_notes", "")), 1000)
        survey.clinical_done = True
    except ValueError as exc:
        return _render(
            request,
            "survey_clinical.html",
            user,
            status_code=400,
            survey=survey,
            cmv_requirements=CMV_REQUIREMENTS,
            error=str(exc),
        )
    db.commit()
    _flash(request, "Clinical survey saved.")
    return _redirect("/couple/survey/preferences")


@router.get("/couple/survey/preferences")
def preferences_form(request: Request):
    """Second survey step."""
    try:
        user = _require_consent(request, "couple")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    return _render(
        request,
        "survey_preferences.html",
        user,
        survey=get_or_create_survey(_db(request), user.id),
        id_release=ID_RELEASE,
    )


@router.post("/couple/survey/preferences")
async def preferences_submit(request: Request):
    """Save preference weights."""
    form = await _form(request)
    try:
        user = _require_consent(request, "couple")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    db = _db(request)
    survey = get_or_create_survey(db, user.id)
    try:
        survey.id_release = _choice(str(form.get("id_release", "")), ID_RELEASE, "ID-release preference")
        survey.family_limit = _bounded_int(str(form.get("family_limit", "")), "Family limit", 1, 25)
        survey.preferences_done = True
    except ValueError as exc:
        return _render(
            request,
            "survey_preferences.html",
            user,
            status_code=400,
            survey=survey,
            id_release=ID_RELEASE,
            error=str(exc),
        )
    db.commit()
    _flash(request, "Preferences saved.")
    return _redirect("/match")


@router.get("/match")
def match_list(request: Request):
    """Ranked donor list. Medical conflicts render first on each row."""
    try:
        user = _require_consent(request, "couple")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    db = _db(request)
    survey = get_or_create_survey(db, user.id)
    rows = ranked_matches(db, user, request.app.state.settings)
    return _render(request, "match_list.html", user, rows=rows, survey=survey)


@router.get("/match/{donor_id}")
def match_detail(request: Request, donor_id: int):
    """One donor with an optional stored explanation."""
    try:
        user = _require_consent(request, "couple")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    db = _db(request)
    found = find_ranked_donor(ranked_matches(db, user, request.app.state.settings), donor_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Donor not found.")
    donor, result = found
    return _render(
        request,
        "match_detail.html",
        user,
        donor=donor,
        result=result,
        sentences=latest_explanation(db, user.id, donor.id),
    )


@router.post("/match/{donor_id}/explain")
async def match_explain(request: Request, donor_id: int):
    """Write a cited explanation from the record."""
    await _form(request)
    try:
        user = _require_consent(request, "couple")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    db = _db(request)
    found = find_ranked_donor(ranked_matches(db, user, request.app.state.settings), donor_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Donor not found.")
    donor, result = found
    packet = packet_for(db, user, donor, result)
    sentences = explain(packet, request.app.state.settings)
    store_explanation(db, user.id, donor.id, sentences)
    db.commit()
    return _redirect(f"/match/{donor.id}")


@router.get("/bank")
def bank_home(request: Request):
    """Donor inventory for the signed-in bank."""
    try:
        user = _require_consent(request, "bank")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    donors = list(
        _db(request).scalars(select(Donor).where(Donor.bank_user_id == user.id).order_by(Donor.code))
    )
    return _render(request, "bank.html", user, donors=donors)


def _donor_page(request: Request, user: User, donor: Donor | None, error: str = "", status_code: int = 200):
    carriers = [] if donor is None or donor.id is None else list_carriers(_db(request), "donor", donor.id)
    return _render(
        request,
        "donor_form.html",
        user,
        status_code=status_code,
        donor=donor,
        carriers=carriers,
        error=error,
        blood_types=BLOOD_TYPES,
        rh_values=RH_VALUES,
        photo_keys=PHOTO_KEYS,
        cmv_status=CMV_STATUS,
        quarantine=QUARANTINE,
        id_release=ID_RELEASE,
        zygosities=ZYGOSITIES,
    )


def _apply_donor_form(donor: Donor, form) -> None:
    donor.code = _clean_text(str(form.get("code", "")), 64)
    if not donor.code:
        raise ValueError("Enter a donor code.")
    donor.blood_type = _choice(str(form.get("blood_type", "")), BLOOD_TYPES, "blood type")
    donor.rh = _choice(str(form.get("rh", "")), RH_VALUES, "Rh status")
    donor.ancestry = _clean_text(str(form.get("ancestry", "")), 255)
    donor.photo_key = _choice(str(form.get("photo_key", "")), PHOTO_KEYS, "synthetic portrait")
    donor.panel = _clean_text(str(form.get("panel", "")), 128)
    donor.cmv = _choice(str(form.get("cmv", "")), CMV_STATUS, "CMV status")
    donor.quarantine = _choice(str(form.get("quarantine", "")), QUARANTINE, "quarantine status")
    donor.family_limit = _bounded_int(str(form.get("family_limit", "")), "Family limit", 1, 25)
    donor.id_release_policy = _choice(str(form.get("id_release_policy", "")), ID_RELEASE, "ID-release policy")
    donor.catalog_confirmed = True


@router.get("/bank/donors/new")
def donor_new(request: Request):
    """Blank donor form."""
    try:
        user = _require_consent(request, "bank")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    blank = Donor(
        bank_user_id=user.id,
        code="",
        blood_type="O",
        rh="negative",
        ancestry="",
        photo_key="geo-1",
        panel="",
        cmv="unknown",
        quarantine="cleared",
        family_limit=10,
        id_release_policy="either",
    )
    return _donor_page(request, user, blank)


@router.post("/bank/donors/new")
async def donor_create(request: Request):
    """Create a donor from the bank survey form."""
    form = await _form(request)
    try:
        user = _require_consent(request, "bank")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    donor = Donor(bank_user_id=user.id, code="")
    try:
        _apply_donor_form(donor, form)
    except ValueError as exc:
        return _donor_page(request, user, donor, error=str(exc), status_code=400)
    db = _db(request)
    db.add(donor)
    db.commit()
    _flash(request, "Donor saved.")
    return _redirect(f"/bank/donors/{donor.id}")


@router.get("/bank/donors/{donor_id}")
def donor_edit(request: Request, donor_id: int):
    """Edit one donor owned by this bank."""
    try:
        user = _require_consent(request, "bank")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    donor = _owned_donor(_db(request), user.id, donor_id)
    return _donor_page(request, user, donor)


@router.post("/bank/donors/{donor_id}")
async def donor_update(request: Request, donor_id: int):
    """Update the per-donor bank survey."""
    form = await _form(request)
    try:
        user = _require_consent(request, "bank")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    db = _db(request)
    donor = _owned_donor(db, user.id, donor_id)
    try:
        _apply_donor_form(donor, form)
    except ValueError as exc:
        return _donor_page(request, user, donor, error=str(exc), status_code=400)
    db.commit()
    _flash(request, "Donor saved.")
    return _redirect(f"/bank/donors/{donor.id}")


@router.post("/bank/donors/{donor_id}/carriers")
async def donor_carrier_add(request: Request, donor_id: int):
    """Add a carrier row to a donor."""
    form = await _form(request)
    try:
        user = _require_consent(request, "bank")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    db = _db(request)
    donor = _owned_donor(db, user.id, donor_id)
    try:
        gene, zygosity, condition = _carrier_fields(form)
        db.add(
            Carrier(
                subject_type="donor",
                subject_id=donor.id,
                gene=gene,
                zygosity=zygosity,
                condition=condition,
            )
        )
        db.commit()
    except ValueError as exc:
        return _donor_page(request, user, donor, error=str(exc), status_code=400)
    _flash(request, "Carrier result saved.")
    return _redirect(f"/bank/donors/{donor.id}")


@router.post("/bank/donors/{donor_id}/delete")
async def donor_delete(request: Request, donor_id: int):
    """Delete a donor and that donor's carrier rows."""
    await _form(request)
    try:
        user = _require_consent(request, "bank")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    db = _db(request)
    donor = _owned_donor(db, user.id, donor_id)
    for row in list_carriers(db, "donor", donor.id):
        db.delete(row)
    db.delete(donor)
    db.commit()
    _flash(request, "Donor removed.")
    return _redirect("/bank")


def _owned_donor(db: Session, bank_user_id: int, donor_id: int) -> Donor:
    donor = db.get(Donor, donor_id)
    if donor is None or donor.bank_user_id != bank_user_id:
        raise HTTPException(status_code=404, detail="Donor not found.")
    return donor


def _drafts_from_session(request: Request) -> list[dict[str, str]]:
    drafts = request.session.get("catalog_draft")
    if not isinstance(drafts, list):
        return []
    return [row for row in drafts if isinstance(row, dict)]


@router.get("/bank/catalog")
def catalog_form(request: Request):
    """Catalog URL form and the confirm step."""
    try:
        user = _require_consent(request, "bank")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    return _render(request, "catalog.html", user, drafts=_drafts_from_session(request), error="")


@router.post("/bank/catalog/sample")
async def catalog_sample(request: Request):
    """Load the bundled synthetic catalog through the same parser."""
    await _form(request)
    try:
        user = _require_consent(request, "bank")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    html = FIXTURE_CATALOG.read_text(encoding="utf-8")
    request.session["catalog_draft"] = [_donor_draft(row) for row in parse_catalog(html)]
    request.session["catalog_source"] = "bundled-sample"
    _flash(request, "Sample catalog parsed. Confirm the fields before they are saved.")
    return _redirect("/bank/catalog")


@router.post("/bank/catalog/fetch")
async def catalog_fetch(request: Request):
    """Fetch a public catalog URL. Images are not kept."""
    form = await _form(request)
    try:
        user = _require_consent(request, "bank")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    url = str(form.get("url", "")).strip()
    try:
        import httpx

        with httpx.Client() as client:
            html = fetch_catalog_html(url, client)
        drafts = [_donor_draft(row) for row in parse_catalog(html)]
        if not drafts:
            raise ValueError("No donor records were found on that page.")
    except ValueError as exc:
        return _render(
            request,
            "catalog.html",
            user,
            status_code=400,
            drafts=[],
            error=str(exc),
        )
    request.session["catalog_draft"] = drafts
    request.session["catalog_source"] = url
    _flash(request, "Catalog parsed. Confirm the fields before they affect a score.")
    return _redirect("/bank/catalog")


@router.post("/bank/catalog/confirm")
async def catalog_confirm(request: Request):
    """Save the selected parsed rows. Portraits stay synthetic."""
    form = await _form(request)
    try:
        user = _require_consent(request, "bank")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    drafts = _drafts_from_session(request)
    selected = {str(value) for value in form.getlist("code")}
    if not selected:
        return _render(
            request,
            "catalog.html",
            user,
            status_code=400,
            drafts=drafts,
            error="Select at least one donor to save.",
        )
    db = _db(request)
    source = str(request.session.get("catalog_source", ""))
    saved = 0
    for index, draft in enumerate(drafts):
        if draft.get("code") not in selected:
            continue
        donor = _donor_from_draft(user.id, draft, index, source)
        db.add(donor)
        saved += 1
    db.commit()
    request.session.pop("catalog_draft", None)
    request.session.pop("catalog_source", None)
    _flash(request, f"Saved {saved} donor record(s). Portraits are synthetic.")
    return _redirect("/bank")


def _donor_draft(row: CatalogDonor) -> dict[str, str]:
    """Session-safe fields. Image locations are not included."""
    return {
        "code": row.code,
        "ancestry": row.ancestry,
        "blood_type": row.blood_type,
        "rh": row.rh,
        "cmv": row.cmv,
        "panel": row.panel,
        "quarantine": row.quarantine,
        "family_limit": row.family_limit,
        "id_release_policy": row.id_release_policy,
    }


def _donor_from_draft(bank_user_id: int, draft: dict[str, str], index: int, source: str) -> Donor:
    blood = draft.get("blood_type", "")
    rh = draft.get("rh", "")
    cmv = draft.get("cmv", "")
    quarantine = draft.get("quarantine", "")
    id_release = draft.get("id_release_policy", "")
    try:
        family_limit = int(draft.get("family_limit", "1"))
    except ValueError:
        family_limit = 1
    family_limit = min(max(family_limit, 1), 25)
    return Donor(
        bank_user_id=bank_user_id,
        code=_clean_text(draft.get("code", ""), 64) or f"CAT-{index + 1}",
        blood_type=blood if blood in BLOOD_TYPES else "",
        rh=rh if rh in RH_VALUES else "",
        ancestry=_clean_text(draft.get("ancestry", ""), 255),
        photo_key=PHOTO_KEYS[index % len(PHOTO_KEYS)],
        panel=_clean_text(draft.get("panel", ""), 128),
        cmv=cmv if cmv in CMV_STATUS else "unknown",
        quarantine=quarantine if quarantine in QUARANTINE else "cleared",
        family_limit=family_limit,
        id_release_policy=id_release if id_release in ID_RELEASE else "either",
        catalog_source_url=source if source != "bundled-sample" else "",
        catalog_confirmed=True,
    )
