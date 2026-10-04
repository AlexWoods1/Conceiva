"""HTTP routes for the couple, sperm-bank, and counselor site."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.formparsers import MultiPartException
from starlette.requests import ClientDisconnect

from app.constants import (
    ADULT_PHOTO_KEYS,
    APPOINTMENT_BOOKED,
    BABY_PHOTO_KEYS,
    BLOOD_TYPES,
    COMMON_CARRIERS,
    CMV_REQUIREMENT_LABELS,
    CMV_REQUIREMENTS,
    CMV_STATUS,
    CMV_STATUS_LABELS,
    DEFAULT_SLOT_MINUTES,
    EYE_COLORS,
    HAIR_COLORS,
    HAIR_TYPES,
    HEIGHT_CM_MAX,
    HEIGHT_CM_MIN,
    ID_RELEASE,
    ID_RELEASE_LABELS,
    MAX_MOTILITY_UPLOAD_BYTES,
    MAX_SHORTLIST,
    MOTILITY_DISCLAIMER,
    MOTILITY_METRIC_HELP,
    MOTILITY_REVIEW_LABELS,
    MOTILITY_SAMPLE_TIMING,
    MOTILITY_SAMPLE_TIMING_LABELS,
    PHOTO_KEYS,
    QUARANTINE,
    QUARANTINE_LABELS,
    RH_LABELS,
    RH_VALUES,
    ROLES,
    WEIGHT_KG_MAX,
    WEIGHT_KG_MIN,
    WHO_PROGRESSIVE_MOTILITY_MIN_PCT,
    WHO_TOTAL_MOTILITY_MIN_PCT,
    ZYGOSITIES,
    ZYGOSITY_LABELS,
)
from app.billing import (
    create_checkout_session,
    handle_webhook_event,
    parse_webhook_event,
    retrieve_and_fulfill,
)
from app.llm import explain
from app.models import (
    Appointment,
    AvailabilitySlot,
    Carrier,
    ContactMessage,
    Donor,
    MotilitySample,
    User,
)
from app.motility_client import MotilityServiceError, analyze_donor_video
from app.scrape import CatalogDonor, fetch_catalog_html, parse_catalog
from app.security import hash_password, verify_password
from app.seed import (
    DEMO_BANK_EMAIL,
    DEMO_BANK_PASSWORD,
    DEMO_BLURBS,
    DEMO_COUNSELOR_EMAIL,
    DEMO_COUNSELOR_PASSWORD,
    DEMO_COUPLE_EMAIL,
    DEMO_COUPLE_PASSWORD,
)
from app.services import (
    add_to_shortlist,
    appointment_donor_ids,
    book_appointment,
    cancel_appointment,
    counselor_display_name,
    counselors_with_open_slots,
    delete_account,
    delete_donor,
    find_ranked_donor,
    get_or_create_counselor_profile,
    get_or_create_history,
    get_or_create_profile,
    get_or_create_survey,
    home_path_for,
    latest_candidate_report,
    latest_explanation,
    list_carriers,
    list_shortlist,
    packet_for,
    ranked_matches,
    remove_from_shortlist,
    run_candidate_report,
    score_donor_for_couple,
    shortlist_donor_ids,
    slot_is_open,
    store_explanation,
)
from app.traits import (
    donor_passes,
    format_height,
    format_weight,
    parse_trait_filter,
    trait_summary,
)

router = APIRouter()
TEMPLATES = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)
TEMPLATES.env.globals.update(
    rh_labels=RH_LABELS,
    cmv_requirement_labels=CMV_REQUIREMENT_LABELS,
    cmv_status_labels=CMV_STATUS_LABELS,
    id_release_labels=ID_RELEASE_LABELS,
    quarantine_labels=QUARANTINE_LABELS,
    demo_blurbs=DEMO_BLURBS,
    zygosity_labels=ZYGOSITY_LABELS,
    common_carriers=COMMON_CARRIERS,
    trait_summary=trait_summary,
    format_height=format_height,
    format_weight=format_weight,
)
FIXTURE_CATALOG = Path(__file__).resolve().parent / "fixtures" / "sample_catalog.html"


def _csrf(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


def _render(
    request: Request, name: str, user: User | None, status_code: int = 200, **extra
):
    context = {
        "user": user,
        "csrf": _csrf(request),
        "flash": request.session.pop("flash", ""),
        "face_compare": request.app.state.settings.enable_face_compare,
        "demo_couple_email": DEMO_COUPLE_EMAIL,
        "demo_couple_password": DEMO_COUPLE_PASSWORD,
        "demo_bank_email": DEMO_BANK_EMAIL,
        "demo_bank_password": DEMO_BANK_PASSWORD,
        "demo_counselor_email": DEMO_COUNSELOR_EMAIL,
        "demo_counselor_password": DEMO_COUNSELOR_PASSWORD,
        "contact_email": request.app.state.settings.contact_email,
        "max_shortlist": MAX_SHORTLIST,
        **extra,
    }
    return TEMPLATES.TemplateResponse(request, name, context, status_code=status_code)


def _flash(request: Request, message: str) -> None:
    request.session["flash"] = message


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def _wants_json(request: Request) -> bool:
    return "application/json" in request.headers.get("accept", "")


async def _form(request: Request, max_part_size: int | None = None):
    kwargs = {} if max_part_size is None else {"max_part_size": max_part_size}
    try:
        form = await request.form(**kwargs)
    except MultiPartException as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except ClientDisconnect as exc:
        # The browser closed the connection mid-upload. No one is listening
        # for a response; raising HTTPException here would just log a
        # confusing 400 for a client that already left.
        raise HTTPException(status_code=499, detail="Client disconnected.") from exc
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
    user = _db(request).get(User, user_id)
    if user is not None:
        # * Rehydrate consent after Vercel /tmp SQLite reseeds wipe consent_at.
        _sync_consent_from_session(request, user)
    return user


def _sync_consent_from_session(request: Request, user: User) -> None:
    """Restore consent_at from the cookie session when the DB row lost it."""
    if user.consent_at is not None:
        if request.session.get("consent_ok") is not True:
            request.session["consent_ok"] = True
        return
    if request.session.get("consent_ok") is True:
        user.consent_at = datetime.now(timezone.utc)


def _mark_consented(request: Request, user: User) -> None:
    """Persist consent on the user row and in the session (survives /tmp reseeds)."""
    user.consent_at = datetime.now(timezone.utc)
    request.session["consent_ok"] = True


def _require_user(request: Request) -> User:
    user = _user(request)
    if user is None:
        raise _RedirectNeeded("/login")
    return user


def _require_consent(request: Request, role: str) -> User:
    user = _require_user(request)
    if user.role != role:
        raise _RedirectNeeded(home_path_for(user))
    if user.consent_at is None:
        raise _RedirectNeeded("/consent")
    return user


def _require_match_access(request: Request) -> User:
    """Require a consented couple account that has unlocked matching."""
    user = _require_consent(request, "couple")
    settings = request.app.state.settings
    if settings.stripe_enabled and user.paid_at is None:
        raise _RedirectNeeded("/billing/unlock")
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


def _optional_choice(value: str, options: tuple[str, ...], label: str) -> str:
    value = value.strip().lower()
    return "" if not value else _choice(value, options, label)


def _optional_int(value: str, label: str, low: int, high: int) -> int | None:
    value = value.strip()
    return None if not value else _bounded_int(value, label, low, high)


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
        raise ValueError(
            "Enter the condition name from the report, such as cystic fibrosis."
        )
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
    latest = _db(request).scalars(
        select(Donor)
        .where(Donor.catalog_confirmed.is_(True), Donor.baby_photo_key != "")
        .order_by(Donor.id.desc())
        .limit(8)
    )
    return _render(
        request,
        "home.html",
        _user(request),
        latest_donors=list(latest),
        hair_colors=HAIR_COLORS,
        eye_colors=EYE_COLORS,
        carrier_gene_count=len(COMMON_CARRIERS),
    )


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
    if user.consent_at is not None:
        request.session["consent_ok"] = True
    else:
        request.session.pop("consent_ok", None)
        return _redirect("/consent")
    return _redirect(home_path_for(user))


@router.post("/register")
async def register_submit(request: Request):
    """Create a couple login. Bank and counselor accounts come from seed only."""
    form = await _form(request)
    email = str(form.get("email", "")).strip().lower()
    password = str(form.get("password", ""))
    role = str(form.get("role", "couple"))
    error = ""
    if role != "couple":
        error = (
            "Public signup is for couples only. Use the demo bank or counselor login."
        )
    elif "@" not in email or len(email) > 255:
        error = "Enter an email address."
    elif len(password) < 8:
        error = "Use a password of at least 8 characters."
    elif _db(request).scalars(select(User).where(User.email == email)).first():
        error = "That email is already registered."
    if error:
        return _render(
            request, "login.html", None, status_code=400, role="couple", error=error
        )
    db = _db(request)
    user = User(email=email, password_hash=hash_password(password), role="couple")
    db.add(user)
    db.commit()
    db.refresh(user)
    request.session["user_id"] = user.id
    request.session.pop("consent_ok", None)
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
    if user.consent_at is not None:
        return _redirect(home_path_for(user))
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
    _mark_consented(request, user)
    return _redirect(home_path_for(user))


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
        profile.blood_type = _choice(
            str(form.get("blood_type", "")), BLOOD_TYPES, "blood type"
        )
        profile.rh = _choice(str(form.get("rh", "")), RH_VALUES, "Rh status")
        history.prior_pregnancies = _bounded_int(
            str(form.get("prior_pregnancies", "0")), "Prior pregnancies", 0, 30
        )
        history.miscarriages = _bounded_int(
            str(form.get("miscarriages", "0")), "Miscarriages", 0, 30
        )
        history.prior_donors = _clean_text(str(form.get("prior_donors", "")), 500)
        history.known_conditions = _clean_text(
            str(form.get("known_conditions", "")), 500
        )
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
        survey.cmv_requirement = _choice(
            str(form.get("cmv_requirement", "")), CMV_REQUIREMENTS, "CMV requirement"
        )
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
        survey.id_release = _choice(
            str(form.get("id_release", "")), ID_RELEASE, "ID-release preference"
        )
        survey.family_limit = _bounded_int(
            str(form.get("family_limit", "")), "Family limit", 1, 25
        )
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
        user = _require_match_access(request)
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    db = _db(request)
    survey = get_or_create_survey(db, user.id)
    all_rows = ranked_matches(db, user, request.app.state.settings)
    trait_filter = parse_trait_filter(request.query_params)
    rows = [row for row in all_rows if donor_passes(row[0], trait_filter)]
    return _render(
        request,
        "match_list.html",
        user,
        rows=rows,
        survey=survey,
        shortlisted_ids=shortlist_donor_ids(db, user.id),
        shortlist_count=len(list_shortlist(db, user.id)),
        trait_filter=trait_filter,
        hidden_count=len(all_rows) - len(rows),
        hair_colors=HAIR_COLORS,
        hair_types=HAIR_TYPES,
        eye_colors=EYE_COLORS,
        height_min=HEIGHT_CM_MIN,
        height_max=HEIGHT_CM_MAX,
    )


@router.get("/match/{donor_id}")
def match_detail(request: Request, donor_id: int):
    """One donor with an optional stored explanation."""
    try:
        user = _require_match_access(request)
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    db = _db(request)
    found = find_ranked_donor(
        ranked_matches(db, user, request.app.state.settings), donor_id
    )
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
        user = _require_match_access(request)
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    db = _db(request)
    found = find_ranked_donor(
        ranked_matches(db, user, request.app.state.settings), donor_id
    )
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
        _db(request).scalars(
            select(Donor).where(Donor.bank_user_id == user.id).order_by(Donor.code)
        )
    )
    return _render(request, "bank.html", user, donors=donors)


def _named_clip(form, name: str):
    """Return an uploaded file, or None when that side was left empty."""
    item = form.get(name)
    if item is None or not getattr(item, "filename", ""):
        return None
    return item


def _motility_uploads(form) -> list[tuple[str, object]] | str:
    """Pair each uploaded clip with fresh or post_thaw.

    A single field named video, with no side chosen, stays the after-thaw clip
    so older clients keep working.
    """
    fresh = _named_clip(form, "fresh_video")
    thawed = _named_clip(form, "thaw_video")
    clips: list[tuple[str, object]] = []
    if fresh is not None:
        clips.append(("fresh", fresh))
    if thawed is not None:
        clips.append(("post_thaw", thawed))
    if clips:
        return clips
    legacy = _named_clip(form, "video")
    if legacy is None:
        return (
            "Choose a microscope clip for the pre-freeze sample, the "
            "post-thaw sample, or both."
        )
    timing = str(form.get("sample_timing", "post_thaw") or "post_thaw")
    if timing not in MOTILITY_SAMPLE_TIMING:
        return "Say whether this clip is pre-freeze or post-thaw."
    return [(timing, legacy)]


def _optional_summary_float(summary: dict, key: str) -> float | None:
    """Return a float from the analyzer summary, or None when it is absent."""
    value = summary.get(key)
    if value is None or value == "":
        return None
    return float(value)


def _optional_summary_int(summary: dict, key: str) -> int | None:
    """Return an int from the analyzer summary, or None when it is absent."""
    value = summary.get(key)
    if value is None or value == "":
        return None
    return int(value)


def _motility_change_phrase(change_points: float) -> str:
    """Plain description of how progressive motility moved after freezing."""
    if change_points < -0.05:
        return f"down {abs(change_points):.1f} percentage points"
    if change_points > 0.05:
        return f"up {change_points:.1f} percentage points"
    return "unchanged"


def _motility_split(summary: dict) -> dict[str, float | int | bool | None]:
    """Read the analyzer summary, filling the split when a field is omitted.

    Kinematic fields are stored only when the summary includes them. VAP,
    STR, WOB, and LIN are not in that summary, so they are not stored.
    """
    total = float(summary["total_motility_percent"])
    progressive = float(summary["percent_progressive"])
    non_progressive = summary.get("percent_non_progressive")
    immotile = summary.get("percent_immotile")
    tracks = summary.get("num_tracks_analyzed")
    scale = summary.get("scale_is_approximate")
    return {
        "total_pct": total,
        "progressive_pct": progressive,
        "non_progressive_pct": (
            float(non_progressive) if non_progressive is not None else total - progressive
        ),
        "immotile_pct": float(immotile) if immotile is not None else 100.0 - total,
        "track_count": int(tracks) if tracks is not None else None,
        "mean_vcl": _optional_summary_float(summary, "mean_vcl"),
        "mean_vsl": _optional_summary_float(summary, "mean_vsl"),
        "fps": _optional_summary_float(summary, "fps"),
        "scale_is_approximate": None if scale is None else bool(scale),
        "cluster_count": _optional_summary_int(summary, "cluster_count"),
    }


def _mirror_motility(donor: Donor, timing: str, numbers: dict, video_url: str) -> None:
    """Keep the couple-facing donor columns on the clip just saved.

    Matching still reads one snapshot. A bank page that has both clips uses
    the motility_samples rows, not these columns.
    """
    donor.motility_total_pct = numbers["total_pct"]
    donor.motility_progressive_pct = numbers["progressive_pct"]
    donor.motility_non_progressive_pct = numbers["non_progressive_pct"]
    donor.motility_immotile_pct = numbers["immotile_pct"]
    donor.motility_track_count = numbers["track_count"]
    donor.motility_sample_timing = timing
    donor.motility_video_url = video_url
    donor.motility_below_reference = (
        numbers["total_pct"] < WHO_TOTAL_MOTILITY_MIN_PCT
        or numbers["progressive_pct"] < WHO_PROGRESSIVE_MOTILITY_MIN_PCT
    )


def _save_motility_sample(
    db: Session, donor: Donor, timing: str, numbers: dict, video_url: str
) -> MotilitySample:
    """Insert or replace the fresh or after-thaw clip without touching the other."""
    sample = db.scalars(
        select(MotilitySample).where(
            MotilitySample.donor_id == donor.id, MotilitySample.timing == timing
        )
    ).first()
    if sample is None:
        sample = MotilitySample(donor_id=donor.id, timing=timing)
        db.add(sample)
    sample.total_pct = numbers["total_pct"]
    sample.progressive_pct = numbers["progressive_pct"]
    sample.non_progressive_pct = numbers["non_progressive_pct"]
    sample.immotile_pct = numbers["immotile_pct"]
    sample.track_count = numbers["track_count"]
    sample.video_url = video_url
    sample.mean_vcl = numbers.get("mean_vcl")
    sample.mean_vsl = numbers.get("mean_vsl")
    sample.fps = numbers.get("fps")
    sample.scale_is_approximate = numbers.get("scale_is_approximate")
    sample.cluster_count = numbers.get("cluster_count")
    donor.motility_review_status = "awaiting_review"
    _mirror_motility(donor, timing, numbers, video_url)
    return sample


def _backfill_motility_sample(db: Session, donor: Donor) -> None:
    """Copy an older single result into the side it was labeled with."""
    if donor.motility_total_pct is None:
        return
    if donor.motility_sample_timing not in MOTILITY_SAMPLE_TIMING:
        return
    existing = db.scalars(
        select(MotilitySample.id).where(
            MotilitySample.donor_id == donor.id,
            MotilitySample.timing == donor.motility_sample_timing,
        )
    ).first()
    if existing is not None:
        return
    total = donor.motility_total_pct
    progressive = donor.motility_progressive_pct
    if progressive is None:
        return
    non_progressive = donor.motility_non_progressive_pct
    immotile = donor.motility_immotile_pct
    db.add(
        MotilitySample(
            donor_id=donor.id,
            timing=donor.motility_sample_timing,
            total_pct=total,
            progressive_pct=progressive,
            non_progressive_pct=(
                non_progressive
                if non_progressive is not None
                else total - progressive
            ),
            immotile_pct=immotile if immotile is not None else 100.0 - total,
            track_count=donor.motility_track_count,
            video_url=donor.motility_video_url,
        )
    )
    db.commit()


def _motility_comparison(
    fresh: MotilitySample | None, thawed: MotilitySample | None
) -> dict[str, float | str] | None:
    """Progressive motility retention is post-thaw progressive divided by pre-freeze.

    The ratio compares the two samples. It is not evidence that the same
    sperm cells survived freezing. It is omitted when pre-freeze progressive
    motility is zero.
    """
    if fresh is None or thawed is None:
        return None
    if fresh.progressive_pct is None or thawed.progressive_pct is None:
        return None
    change = thawed.progressive_pct - fresh.progressive_pct
    comparison: dict[str, float | str] = {
        "fresh_progressive": fresh.progressive_pct,
        "thaw_progressive": thawed.progressive_pct,
        "change_points": change,
        "fresh_label": f"{fresh.progressive_pct:.1f}%",
        "thaw_label": f"{thawed.progressive_pct:.1f}%",
        "change_label": f"{change:+.1f} percentage points",
    }
    if fresh.progressive_pct > 0:
        retention = thawed.progressive_pct / fresh.progressive_pct * 100.0
        comparison["retention_pct"] = retention
        comparison["retention_label"] = f"{retention:.1f}%"
    return comparison


def _fmt_metric(value: float | int | None, formatter) -> str:
    """Format a stored metric, or an em dash when this clip has no value."""
    if value is None:
        return "—"
    return formatter(value)


def _metric_cell(sample: MotilitySample | None, attr: str, formatter) -> str:
    if sample is None:
        return "—"
    return _fmt_metric(getattr(sample, attr), formatter)


def _metric_row(
    key: str,
    label: str,
    fresh: MotilitySample | None,
    thawed: MotilitySample | None,
    attr: str,
    formatter,
    fresh_below: bool = False,
) -> dict:
    return {
        "kind": "metric",
        "key": key,
        "label": label,
        "tip": MOTILITY_METRIC_HELP[key],
        "fresh": _metric_cell(fresh, attr, formatter),
        "thaw": _metric_cell(thawed, attr, formatter),
        "fresh_below": fresh_below,
    }


def _motility_rows(
    fresh: MotilitySample | None, thawed: MotilitySample | None
) -> list[dict]:
    """Comparison rows limited to fields the analyzer actually returns."""
    pct = lambda value: f"{value:.1f}%"
    speed = lambda value: f"{value:.1f}"
    count = lambda value: str(int(value))
    fresh_below = (
        fresh is not None
        and fresh.progressive_pct is not None
        and fresh.progressive_pct < WHO_PROGRESSIVE_MOTILITY_MIN_PCT
    )
    return [
        {"kind": "group", "label": "Motility"},
        _metric_row(
            "progressive",
            "Progressive motility",
            fresh,
            thawed,
            "progressive_pct",
            pct,
            fresh_below=fresh_below,
        ),
        _metric_row(
            "non_progressive",
            "Non-progressive motility",
            fresh,
            thawed,
            "non_progressive_pct",
            pct,
        ),
        _metric_row("immotile", "Immotile", fresh, thawed, "immotile_pct", pct),
        _metric_row(
            "total", "Total motility", fresh, thawed, "total_pct", pct
        ),
        _metric_row(
            "tracks", "Tracks analyzed", fresh, thawed, "track_count", count
        ),
        {"kind": "group", "label": "Kinematics"},
        _metric_row(
            "vcl", "Mean VCL (µm/s)", fresh, thawed, "mean_vcl", speed
        ),
        _metric_row(
            "vsl", "Mean VSL (µm/s)", fresh, thawed, "mean_vsl", speed
        ),
    ]


def _quality_rows(
    fresh: MotilitySample | None, thawed: MotilitySample | None
) -> list[dict]:
    """Quality fields the summary includes. Duration and frame count are absent."""
    count = lambda value: str(int(value))
    fps = lambda value: f"{value:.2f}"
    rows = [
        _metric_row(
            "tracks", "Tracks analyzed", fresh, thawed, "track_count", count
        ),
        _metric_row("fps", "Frame rate (fps)", fresh, thawed, "fps", fps),
    ]
    if any(
        sample is not None and sample.cluster_count is not None
        for sample in (fresh, thawed)
    ):
        rows.append(
            _metric_row(
                "clusters",
                "Cluster tracks excluded",
                fresh,
                thawed,
                "cluster_count",
                count,
            )
        )
    return rows


def _review_status(donor: Donor, has_analysis: bool) -> str:
    if not has_analysis:
        return "none"
    status = (donor.motility_review_status or "").strip()
    if status not in {"awaiting_review", "accepted", "flagged"}:
        return "awaiting_review"
    return status


def _motility_sides(db: Session, donor: Donor | None) -> dict:
    """Pre-freeze clip, post-thaw clip, and the change between them."""
    if donor is None or donor.id is None:
        return {
            "motility_fresh": None,
            "motility_thaw": None,
            "motility_comparison": None,
            "motility_untimed": False,
            "motility_rows": _motility_rows(None, None),
            "motility_quality_rows": _quality_rows(None, None),
            "motility_scale_is_approximate": False,
            "motility_has_analysis": False,
            "motility_review_status": "none",
            "motility_review_label": MOTILITY_REVIEW_LABELS["none"],
        }
    _backfill_motility_sample(db, donor)
    rows = list(
        db.scalars(select(MotilitySample).where(MotilitySample.donor_id == donor.id))
    )
    by_timing = {row.timing: row for row in rows}
    fresh = by_timing.get("fresh")
    thawed = by_timing.get("post_thaw")
    has_analysis = fresh is not None or thawed is not None
    status = _review_status(donor, has_analysis)
    untimed = (
        donor.motility_total_pct is not None
        and fresh is None
        and thawed is None
        and donor.motility_sample_timing not in MOTILITY_SAMPLE_TIMING
    )
    return {
        "motility_fresh": fresh,
        "motility_thaw": thawed,
        "motility_comparison": _motility_comparison(fresh, thawed),
        "motility_untimed": untimed,
        "motility_rows": _motility_rows(fresh, thawed),
        "motility_quality_rows": _quality_rows(fresh, thawed),
        "motility_scale_is_approximate": any(
            sample is not None and sample.scale_is_approximate is True
            for sample in (fresh, thawed)
        ),
        "motility_has_analysis": has_analysis,
        "motility_review_status": status,
        "motility_review_label": MOTILITY_REVIEW_LABELS[status],
    }


def _donor_page(
    request: Request,
    user: User,
    donor: Donor | None,
    error: str = "",
    status_code: int = 200,
):
    carriers = (
        []
        if donor is None or donor.id is None
        else list_carriers(_db(request), "donor", donor.id)
    )
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
        rh_labels=RH_LABELS,
        photo_keys=PHOTO_KEYS,
        baby_photo_keys=BABY_PHOTO_KEYS,
        cmv_status=CMV_STATUS,
        cmv_status_labels=CMV_STATUS_LABELS,
        quarantine=QUARANTINE,
        quarantine_labels=QUARANTINE_LABELS,
        id_release=ID_RELEASE,
        zygosities=ZYGOSITIES,
        hair_colors=HAIR_COLORS,
        hair_types=HAIR_TYPES,
        eye_colors=EYE_COLORS,
        height_min=HEIGHT_CM_MIN,
        height_max=HEIGHT_CM_MAX,
        weight_min=WEIGHT_KG_MIN,
        weight_max=WEIGHT_KG_MAX,
        who_progressive_min=WHO_PROGRESSIVE_MOTILITY_MIN_PCT,
        who_total_min=WHO_TOTAL_MOTILITY_MIN_PCT,
        motility_disclaimer=MOTILITY_DISCLAIMER,
        motility_uploads_enabled=request.app.state.settings.motility_uploads_enabled,
        motility_timing_labels=MOTILITY_SAMPLE_TIMING_LABELS,
        **_motility_sides(_db(request), donor),
    )


def _apply_donor_form(donor: Donor, form) -> None:
    donor.code = _clean_text(str(form.get("code", "")), 64)
    if not donor.code:
        raise ValueError("Enter a donor code.")
    donor.blood_type = _choice(
        str(form.get("blood_type", "")), BLOOD_TYPES, "blood type"
    )
    donor.rh = _choice(str(form.get("rh", "")), RH_VALUES, "Rh status")
    donor.ancestry = _clean_text(str(form.get("ancestry", "")), 255)
    donor.photo_key = _choice(
        str(form.get("photo_key", "")), PHOTO_KEYS, "synthetic portrait"
    )
    donor.baby_photo_key = _optional_choice(
        str(form.get("baby_photo_key", "")), BABY_PHOTO_KEYS, "childhood photo"
    )
    donor.panel = _clean_text(str(form.get("panel", "")), 128)
    donor.cmv = _choice(str(form.get("cmv", "")), CMV_STATUS, "CMV status")
    donor.quarantine = _choice(
        str(form.get("quarantine", "")), QUARANTINE, "quarantine status"
    )
    donor.family_limit = _bounded_int(
        str(form.get("family_limit", "")), "Family limit", 1, 25
    )
    donor.id_release_policy = _choice(
        str(form.get("id_release_policy", "")), ID_RELEASE, "ID-release policy"
    )
    donor.hair_color = _optional_choice(
        str(form.get("hair_color", "")), HAIR_COLORS, "hair color"
    )
    donor.hair_type = _optional_choice(
        str(form.get("hair_type", "")), HAIR_TYPES, "hair type"
    )
    donor.eye_color = _optional_choice(
        str(form.get("eye_color", "")), EYE_COLORS, "eye color"
    )
    donor.height_cm = _optional_int(
        str(form.get("height_cm", "")), "Height", HEIGHT_CM_MIN, HEIGHT_CM_MAX
    )
    donor.weight_kg = _optional_int(
        str(form.get("weight_kg", "")), "Weight", WEIGHT_KG_MIN, WEIGHT_KG_MAX
    )
    donor.ethnicity = _clean_text(str(form.get("ethnicity", "")), 64)
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
        photo_key="default",
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


@router.post("/bank/donors/{donor_id}/motility")
async def donor_motility_upload(request: Request, donor_id: int):
    """Analyze an uploaded semen sample video and store the result."""
    # max_part_size is enforced against actual bytes received as they
    # stream in, not a client-supplied (and therefore spoofable) header.
    form = await _form(request, max_part_size=MAX_MOTILITY_UPLOAD_BYTES)
    try:
        user = _require_consent(request, "bank")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    db = _db(request)
    donor = _owned_donor(db, user.id, donor_id)
    settings = request.app.state.settings
    if not settings.motility_uploads_enabled:
        return _donor_page(
            request,
            user,
            donor,
            error=(
                "Motility upload is disabled on this host. Run the app and "
                "motility service locally (or on a long-lived server)."
            ),
            status_code=503,
        )
    clips = _motility_uploads(form)
    if isinstance(clips, str):
        return _donor_page(request, user, donor, error=clips, status_code=400)
    # analyze_donor_video() blocks on a synchronous HTTP call for about a
    # minute per clip. Called directly, that freezes this single-threaded
    # event loop. run_in_threadpool moves it off the loop.
    for timing, video in clips:
        video_bytes = await video.read()
        try:
            result = await run_in_threadpool(
                analyze_donor_video, video_bytes, video.filename, settings
            )
        except MotilityServiceError as exc:
            db.commit()
            return _donor_page(
                request, user, donor, error=exc.message, status_code=503
            )
        video_url = str(result.get("annotated_video_url") or "")
        if video_url.startswith("/"):
            video_url = f"{settings.motility_service_url}{video_url}"
        _save_motility_sample(
            db, donor, timing, _motility_split(result["summary"]), video_url
        )
    db.commit()
    fresh = db.scalars(
        select(MotilitySample).where(
            MotilitySample.donor_id == donor.id, MotilitySample.timing == "fresh"
        )
    ).first()
    thawed = db.scalars(
        select(MotilitySample).where(
            MotilitySample.donor_id == donor.id, MotilitySample.timing == "post_thaw"
        )
    ).first()
    comparison = _motility_comparison(fresh, thawed)
    if comparison is not None:
        direction = _motility_change_phrase(comparison["change_points"])
        _flash(
            request,
            (
                "Progressive motility went from "
                f"{comparison['fresh_progressive']:.1f}% pre-freeze to "
                f"{comparison['thaw_progressive']:.1f}% post-thaw, {direction}."
            ),
        )
    elif any(timing == "fresh" for timing, _video in clips):
        _flash(
            request,
            "Pre-freeze clip saved. Add the post-thaw clip to calculate progressive motility retention.",
        )
    else:
        _flash(
            request,
            "Post-thaw clip saved. Add the pre-freeze clip to calculate progressive motility retention.",
        )
    return _redirect(f"/bank/donors/{donor.id}")


_REVIEW_ACTIONS = {
    "accept": "accepted",
    "flag": "flagged",
    "rerun": "awaiting_review",
}


@router.post("/bank/donors/{donor_id}/motility/review")
async def donor_motility_review(request: Request, donor_id: int):
    """Record a technician's review of the automated first pass."""
    form = await _form(request)
    try:
        user = _require_consent(request, "bank")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    db = _db(request)
    donor = _owned_donor(db, user.id, donor_id)
    action = str(form.get("action") or "")
    status = _REVIEW_ACTIONS.get(action)
    if status is None:
        return _donor_page(
            request,
            user,
            donor,
            error="Choose accept, flag, or re-run.",
            status_code=400,
        )
    if not _motility_sides(db, donor)["motility_has_analysis"]:
        return _donor_page(
            request,
            user,
            donor,
            error="Run an analysis before review.",
            status_code=400,
        )
    donor.motility_review_status = status
    db.commit()
    if action == "accept":
        _flash(request, "Analysis accepted.")
    elif action == "flag":
        _flash(request, "Analysis flagged for manual review.")
    else:
        _flash(
            request,
            "Marked awaiting review. Upload the microscope clips again to "
            "re-run. The source file is not kept.",
        )
    return _redirect(f"/bank/donors/{donor.id}")


@router.post("/bank/donors/{donor_id}/delete")
async def donor_delete(request: Request, donor_id: int):
    """Delete a donor and dependent shortlist, visit, and carrier rows."""
    await _form(request)
    try:
        user = _require_consent(request, "bank")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    db = _db(request)
    donor = _owned_donor(db, user.id, donor_id)
    delete_donor(db, donor)
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
    return _render(
        request, "catalog.html", user, drafts=_drafts_from_session(request), error=""
    )


@router.post("/bank/catalog/sample")
async def catalog_sample(request: Request):
    """Load the bundled synthetic catalog through the same parser."""
    await _form(request)
    try:
        user = _require_consent(request, "bank")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    html = FIXTURE_CATALOG.read_text(encoding="utf-8")
    request.session["catalog_draft"] = [
        _donor_draft(row) for row in parse_catalog(html)
    ]
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


def _donor_from_draft(
    bank_user_id: int, draft: dict[str, str], index: int, source: str
) -> Donor:
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


def _parse_slot_start(raw: str) -> datetime:
    """Parse an HTML datetime-local value as UTC."""
    text = raw.strip()
    if not text:
        raise ValueError("Choose a start time.")
    if text.endswith("Z"):
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    else:
        parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _appointment_or_404(db: Session, appointment_id: int) -> Appointment:
    appointment = db.get(Appointment, appointment_id)
    if appointment is None:
        raise HTTPException(status_code=404, detail="Appointment not found.")
    return appointment


def _visit_candidates(
    request: Request, appointment: Appointment
) -> list[tuple[Donor, object, list]]:
    """Live donor rows, match results, and stored reports for a visit."""
    db = _db(request)
    couple = db.get(User, appointment.couple_user_id)
    if couple is None:
        return []
    settings = request.app.state.settings
    rows: list[tuple[Donor, object, list]] = []
    for donor_id in appointment_donor_ids(db, appointment.id):
        donor = db.get(Donor, donor_id)
        if donor is None:
            continue
        result = score_donor_for_couple(db, couple, donor, settings)
        sentences = latest_candidate_report(db, appointment.id, donor.id)
        rows.append((donor, result, sentences))
    return rows


@router.post("/couple/shortlist/{donor_id}/add")
async def shortlist_add(request: Request, donor_id: int):
    """Add a confirmed donor to the couple shortlist."""
    await _form(request)
    try:
        user = _require_consent(request, "couple")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    db = _db(request)
    error = add_to_shortlist(db, user.id, donor_id)
    if not error:
        db.commit()
    message = error or "Added to shortlist."
    if _wants_json(request):
        return JSONResponse(
            {"ok": not error, "message": message}, status_code=400 if error else 200
        )
    _flash(request, message)
    return _redirect("/match")


@router.post("/couple/shortlist/{donor_id}/remove")
async def shortlist_remove(request: Request, donor_id: int):
    """Remove a donor from the couple shortlist."""
    await _form(request)
    try:
        user = _require_consent(request, "couple")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    db = _db(request)
    remove_from_shortlist(db, user.id, donor_id)
    db.commit()
    if _wants_json(request):
        return JSONResponse({"ok": True, "message": "Removed from shortlist."})
    _flash(request, "Removed from shortlist.")
    referer = request.headers.get("referer", "")
    if "/couple/shortlist" in referer:
        return _redirect("/couple/shortlist")
    return _redirect("/match")


@router.get("/couple/shortlist")
def shortlist_page(request: Request):
    """Show the couple's shortlisted candidates."""
    try:
        user = _require_consent(request, "couple")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    db = _db(request)
    settings = request.app.state.settings
    rows = []
    for item in list_shortlist(db, user.id):
        donor = db.get(Donor, item.donor_id)
        if donor is None:
            continue
        result = score_donor_for_couple(db, user, donor, settings)
        carriers = list_carriers(db, "donor", donor.id)
        rows.append((donor, result, carriers))
    return _render(
        request,
        "shortlist.html",
        user,
        rows=rows,
        shortlist_count=len(rows),
    )


@router.get("/couple/book")
def book_form(request: Request):
    """List every counselor with open future slots."""
    try:
        user = _require_consent(request, "couple")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    db = _db(request)
    shortlist_count = len(list_shortlist(db, user.id))
    groups = []
    for counselor, profile, slots in counselors_with_open_slots(db):
        groups.append(
            {
                "counselor": counselor,
                "display_name": profile.display_name.strip() or counselor.email,
                "slots": slots,
            }
        )
    return _render(
        request,
        "book.html",
        user,
        groups=groups,
        shortlist_count=shortlist_count,
    )


@router.post("/couple/book")
async def book_submit(request: Request):
    """Book an open counselor slot with the current shortlist."""
    form = await _form(request)
    try:
        user = _require_consent(request, "couple")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    db = _db(request)
    try:
        slot_id = int(str(form.get("slot_id", "")))
    except ValueError:
        _flash(request, "Choose an open slot.")
        return _redirect("/couple/book")
    slot = db.get(AvailabilitySlot, slot_id)
    if slot is None:
        _flash(request, "That slot is no longer available.")
        return _redirect("/couple/book")
    result = book_appointment(db, user, slot)
    if isinstance(result, str):
        _flash(request, result)
        return _redirect("/couple/book")
    db.commit()
    _flash(request, "Visit booked. The counselor can open your shortlist.")
    return _redirect(f"/couple/appointments/{result.id}")


@router.get("/couple/appointments")
def couple_appointments(request: Request):
    """List the couple's booked visits."""
    try:
        user = _require_consent(request, "couple")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    db = _db(request)
    appointments = list(
        db.scalars(
            select(Appointment)
            .where(Appointment.couple_user_id == user.id)
            .order_by(Appointment.id.desc())
        )
    )
    rows = []
    for appointment in appointments:
        slot = db.get(AvailabilitySlot, appointment.slot_id)
        counselor = db.get(User, appointment.counselor_user_id)
        rows.append(
            {
                "appointment": appointment,
                "slot": slot,
                "counselor_name": (
                    counselor_display_name(db, counselor) if counselor else "Counselor"
                ),
                "donor_count": len(appointment_donor_ids(db, appointment.id)),
            }
        )
    return _render(request, "couple_appointments.html", user, rows=rows)


@router.get("/couple/appointments/{appointment_id}")
def couple_appointment_detail(request: Request, appointment_id: int):
    """Show one booked visit and any counselor reports already run."""
    try:
        user = _require_consent(request, "couple")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    db = _db(request)
    appointment = _appointment_or_404(db, appointment_id)
    if appointment.couple_user_id != user.id:
        raise HTTPException(status_code=404, detail="Appointment not found.")
    slot = db.get(AvailabilitySlot, appointment.slot_id)
    counselor = db.get(User, appointment.counselor_user_id)
    candidates = _visit_candidates(request, appointment)
    return _render(
        request,
        "couple_appointment_detail.html",
        user,
        appointment=appointment,
        slot=slot,
        counselor_name=(
            counselor_display_name(db, counselor) if counselor else "Counselor"
        ),
        candidates=candidates,
    )


@router.post("/couple/appointments/{appointment_id}/cancel")
async def couple_cancel_appointment(request: Request, appointment_id: int):
    """Cancel a couple's booked visit and free the slot."""
    await _form(request)
    try:
        user = _require_consent(request, "couple")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    db = _db(request)
    appointment = _appointment_or_404(db, appointment_id)
    if appointment.couple_user_id != user.id:
        raise HTTPException(status_code=404, detail="Appointment not found.")
    error = cancel_appointment(db, appointment)
    if error:
        _flash(request, error)
    else:
        db.commit()
        _flash(request, "Visit cancelled. The slot is open again.")
    return _redirect(f"/couple/appointments/{appointment.id}")


@router.get("/counselor")
def counselor_home(request: Request):
    """Upcoming booked visits for the signed-in counselor."""
    try:
        user = _require_consent(request, "counselor")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    db = _db(request)
    appointments = list(
        db.scalars(
            select(Appointment)
            .where(Appointment.counselor_user_id == user.id)
            .order_by(Appointment.id.desc())
        )
    )
    rows = []
    for appointment in appointments:
        slot = db.get(AvailabilitySlot, appointment.slot_id)
        couple = db.get(User, appointment.couple_user_id)
        rows.append(
            {
                "appointment": appointment,
                "slot": slot,
                "couple_email": couple.email if couple else "",
                "donor_count": len(appointment_donor_ids(db, appointment.id)),
            }
        )
    return _render(request, "counselor_home.html", user, rows=rows)


@router.get("/counselor/slots")
def counselor_slots(request: Request):
    """List and manage the counselor's availability."""
    try:
        user = _require_consent(request, "counselor")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    db = _db(request)
    profile = get_or_create_counselor_profile(db, user.id)
    slots = list(
        db.scalars(
            select(AvailabilitySlot)
            .where(AvailabilitySlot.counselor_user_id == user.id)
            .order_by(AvailabilitySlot.starts_at, AvailabilitySlot.id)
        )
    )
    taken = {
        row.slot_id
        for row in db.scalars(
            select(Appointment).where(
                Appointment.counselor_user_id == user.id,
                Appointment.status == APPOINTMENT_BOOKED,
            )
        )
    }
    rows = [
        {"slot": slot, "open": slot.id not in taken and slot_is_open(db, slot)}
        for slot in slots
    ]
    return _render(
        request,
        "counselor_slots.html",
        user,
        rows=rows,
        profile=profile,
        default_minutes=DEFAULT_SLOT_MINUTES,
    )


@router.post("/counselor/slots")
async def counselor_slot_create(request: Request):
    """Add a future open slot."""
    form = await _form(request)
    try:
        user = _require_consent(request, "counselor")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    db = _db(request)
    try:
        starts_at = _parse_slot_start(str(form.get("starts_at", "")))
        duration = _bounded_int(
            str(form.get("duration_minutes", str(DEFAULT_SLOT_MINUTES))),
            "Duration",
            15,
            180,
        )
        if starts_at < datetime.now(timezone.utc):
            raise ValueError("Choose a future start time.")
    except ValueError as exc:
        _flash(request, str(exc))
        return _redirect("/counselor/slots")
    db.add(
        AvailabilitySlot(
            counselor_user_id=user.id,
            starts_at=starts_at.replace(tzinfo=None),
            duration_minutes=duration,
        )
    )
    db.commit()
    _flash(request, "Slot added.")
    return _redirect("/counselor/slots")


@router.post("/counselor/slots/{slot_id}/delete")
async def counselor_slot_delete(request: Request, slot_id: int):
    """Remove an open slot the counselor owns."""
    await _form(request)
    try:
        user = _require_consent(request, "counselor")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    db = _db(request)
    slot = db.get(AvailabilitySlot, slot_id)
    if slot is None or slot.counselor_user_id != user.id:
        raise HTTPException(status_code=404, detail="Slot not found.")
    booked = db.scalars(
        select(Appointment.id).where(
            Appointment.slot_id == slot.id,
            Appointment.status == APPOINTMENT_BOOKED,
        )
    ).first()
    if booked is not None:
        _flash(request, "That slot already has a booked visit.")
        return _redirect("/counselor/slots")
    db.delete(slot)
    db.commit()
    _flash(request, "Slot removed.")
    return _redirect("/counselor/slots")


@router.post("/counselor/profile")
async def counselor_profile_save(request: Request):
    """Save the counselor display name used on the book page."""
    form = await _form(request)
    try:
        user = _require_consent(request, "counselor")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    db = _db(request)
    profile = get_or_create_counselor_profile(db, user.id)
    profile.display_name = str(form.get("display_name", "")).strip()[:120]
    db.commit()
    _flash(request, "Display name saved.")
    return _redirect("/counselor/slots")


@router.get("/counselor/appointments/{appointment_id}")
def counselor_appointment_detail(request: Request, appointment_id: int):
    """Session page: live candidate profiles and counselor reports."""
    try:
        user = _require_consent(request, "counselor")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    db = _db(request)
    appointment = _appointment_or_404(db, appointment_id)
    if appointment.counselor_user_id != user.id:
        raise HTTPException(status_code=404, detail="Appointment not found.")
    slot = db.get(AvailabilitySlot, appointment.slot_id)
    couple = db.get(User, appointment.couple_user_id)
    candidates = []
    for donor, result, sentences in _visit_candidates(request, appointment):
        candidates.append(
            {
                "donor": donor,
                "result": result,
                "sentences": sentences,
                "carriers": list_carriers(db, "donor", donor.id),
                "couple_carriers": list_carriers(
                    db, "couple", appointment.couple_user_id
                ),
            }
        )
    return _render(
        request,
        "counselor_appointment.html",
        user,
        appointment=appointment,
        slot=slot,
        couple_email=couple.email if couple else "",
        candidates=candidates,
    )


@router.post("/counselor/appointments/{appointment_id}/cancel")
async def counselor_cancel_appointment(request: Request, appointment_id: int):
    """Cancel a counselor's booked visit and free the slot."""
    await _form(request)
    try:
        user = _require_consent(request, "counselor")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    db = _db(request)
    appointment = _appointment_or_404(db, appointment_id)
    if appointment.counselor_user_id != user.id:
        raise HTTPException(status_code=404, detail="Appointment not found.")
    error = cancel_appointment(db, appointment)
    if error:
        _flash(request, error)
    else:
        db.commit()
        _flash(request, "Visit cancelled. The slot is open again.")
    return _redirect(f"/counselor/appointments/{appointment.id}")


@router.post("/counselor/appointments/{appointment_id}/report")
async def counselor_report_all(request: Request, appointment_id: int):
    """Run the AI tool for every candidate on the visit."""
    await _form(request)
    try:
        user = _require_consent(request, "counselor")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    db = _db(request)
    appointment = _appointment_or_404(db, appointment_id)
    if appointment.counselor_user_id != user.id:
        raise HTTPException(status_code=404, detail="Appointment not found.")
    if appointment.status != APPOINTMENT_BOOKED:
        _flash(request, "Cancelled visits cannot generate new reports.")
        return _redirect(f"/counselor/appointments/{appointment.id}")
    settings = request.app.state.settings
    count = 0
    for donor_id in appointment_donor_ids(db, appointment.id):
        donor = db.get(Donor, donor_id)
        if donor is None:
            continue
        run_candidate_report(db, appointment, donor, settings)
        count += 1
    db.commit()
    _flash(request, f"Generated {count} candidate report(s).")
    return _redirect(f"/counselor/appointments/{appointment.id}")


@router.post("/counselor/appointments/{appointment_id}/donors/{donor_id}/report")
async def counselor_report_one(request: Request, appointment_id: int, donor_id: int):
    """Run the AI tool for one visit candidate."""
    await _form(request)
    try:
        user = _require_consent(request, "counselor")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    db = _db(request)
    appointment = _appointment_or_404(db, appointment_id)
    if appointment.counselor_user_id != user.id:
        raise HTTPException(status_code=404, detail="Appointment not found.")
    if donor_id not in appointment_donor_ids(db, appointment.id):
        raise HTTPException(status_code=404, detail="Donor not on this visit.")
    if appointment.status != APPOINTMENT_BOOKED:
        _flash(request, "Cancelled visits cannot generate new reports.")
        return _redirect(f"/counselor/appointments/{appointment.id}")
    donor = db.get(Donor, donor_id)
    if donor is None:
        raise HTTPException(status_code=404, detail="Donor not found.")
    run_candidate_report(db, appointment, donor, request.app.state.settings)
    db.commit()
    _flash(request, f"Report updated for {donor.code}.")
    return _redirect(f"/counselor/appointments/{appointment.id}")


@router.get("/billing/unlock")
def billing_unlock(request: Request):
    """Paywall page for couple accounts that have not completed Checkout."""
    try:
        user = _require_consent(request, "couple")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    if user.paid_at is not None or not request.app.state.settings.stripe_enabled:
        return _redirect("/match")
    return _render(request, "billing_unlock.html", user)


@router.post("/billing/checkout")
async def billing_checkout(request: Request):
    """Start Stripe Checkout for a one-time matching unlock."""
    await _form(request)
    try:
        user = _require_consent(request, "couple")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    settings = request.app.state.settings
    if not settings.stripe_enabled:
        _flash(request, "Billing is not configured on this deployment.")
        return _redirect("/match")
    if user.paid_at is not None:
        return _redirect("/match")
    try:
        url = create_checkout_session(settings, user)
    except Exception:
        _flash(request, "Could not start Checkout. Try again in a moment.")
        return _redirect("/billing/unlock")
    return _redirect(url)


@router.get("/billing/success")
def billing_success(request: Request, session_id: str = ""):
    """Return from Checkout; fulfill if the webhook has not landed yet."""
    try:
        user = _require_consent(request, "couple")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    db = _db(request)
    settings = request.app.state.settings
    if session_id and settings.stripe_enabled and user.paid_at is None:
        try:
            retrieve_and_fulfill(settings, db, session_id)
            db.commit()
        except Exception:
            db.rollback()
    _flash(request, "Donor matches unlocked.")
    return _redirect("/match")


@router.get("/billing/cancel")
def billing_cancel(request: Request):
    """Return from an abandoned Checkout Session."""
    try:
        _require_consent(request, "couple")
    except _RedirectNeeded as needed:
        return _redirect(needed.path)
    _flash(request, "Checkout canceled. Donor matches stay locked until payment.")
    return _redirect("/billing/unlock")


@router.post("/webhooks/stripe")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events for matching unlocks."""
    settings = request.app.state.settings
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    try:
        event = parse_webhook_event(settings, payload, signature)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid webhook.") from exc
    db = _db(request)
    handle_webhook_event(db, event)
    db.commit()
    return {"received": True}
