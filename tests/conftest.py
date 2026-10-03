"""Shared pytest fixtures for the donor-match suite."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import Base
from app.routes import _csrf

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_PASSWORD = "password1"


def _json_render(request, name, user, status_code=200, **extra):
    """Render a route as JSON so tests can run without the HTML templates.

    Args:
        request: Current request. The CSRF token and flash message are read here.
        name: Template name the route selected.
        user: Signed-in user, if any.
        status_code: HTTP status the route asked for.
        **extra: Template context.

    Returns:
        A JSON response carrying the fields tests assert on.
    """
    token = _csrf(request)
    payload = {
        "template": name,
        "csrf": token,
        "flash": request.session.pop("flash", ""),
        "error": extra.get("error", ""),
        "role": extra.get("role"),
        "user_email": None if user is None else user.email,
        "user_role": None if user is None else user.role,
    }
    if "rows" in extra:
        payload["rows"] = [
            {"code": donor.code, "score": result.score, "hard_stop": result.hard_stop}
            for donor, result in extra["rows"]
        ]
    if (
        extra.get("donor") is not None
        and getattr(extra["donor"], "code", None) is not None
    ):
        payload["donor_code"] = extra["donor"].code
    if "sentences" in extra:
        payload["sentences"] = [item.text for item in extra["sentences"]]
    if "drafts" in extra:
        payload["draft_codes"] = [row.get("code") for row in extra["drafts"]]
    if "donors" in extra:
        payload["donor_codes"] = [donor.code for donor in extra["donors"]]
    if "carriers" in extra:
        payload["genes"] = [row.gene for row in extra["carriers"]]
    if "profile" in extra and extra["profile"] is not None:
        payload["blood_type"] = extra["profile"].blood_type
        payload["adult_photo_key"] = extra["profile"].adult_photo_key
    return JSONResponse(payload, status_code=status_code)


class Api:
    """Test client that keeps the CSRF token from JSON page responses."""

    def __init__(self, client: TestClient, app) -> None:
        """Store the client and application.

        Args:
            client: Starlette test client. Redirects are not followed.
            app: FastAPI application under test.
        """
        self.client = client
        self.app = app
        self.csrf = ""

    def get(self, path: str):
        """Send GET and remember a CSRF token when the body includes one.

        Args:
            path: Request path.

        Returns:
            The HTTP response.
        """
        response = self.client.get(path)
        self._capture(response)
        return response

    def post(self, path: str, data: dict | list | None = None):
        """Send a form POST with the current CSRF token.

        Args:
            path: Request path.
            data: Form fields. A list of pairs is encoded as repeated keys.

        Returns:
            The HTTP response.
        """
        if isinstance(data, list):
            # * Repeated keys need an encoded body. httpx rejects a list in data=.
            body = urlencode([("csrf", self.csrf), *data])
            response = self.client.post(
                path,
                content=body,
                headers={"content-type": "application/x-www-form-urlencoded"},
            )
        else:
            response = self.client.post(path, data={"csrf": self.csrf, **(data or {})})
        self._capture(response)
        return response

    def _capture(self, response) -> None:
        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type:
            return
        body = response.json()
        if isinstance(body, dict) and body.get("csrf"):
            self.csrf = body["csrf"]

    def session(self):
        """Open a database session.

        Returns:
            A SQLAlchemy session bound to the application engine.
        """
        return self.app.state.session_factory()


@pytest.fixture(autouse=True)
def static_dir() -> Path:
    """Create the static directory StaticFiles requires at startup.

    Returns:
        Path to the static directory.
    """
    # * StaticFiles rejects a missing directory when the application starts.
    path = PROJECT_ROOT / "static"
    path.mkdir(exist_ok=True)
    return path


@pytest.fixture
def db(tmp_path):
    """Open a SQLite session with the application schema.

    Args:
        tmp_path: Pytest temporary directory.

    Yields:
        A SQLAlchemy session. Closed after the test.
    """
    engine = create_engine(
        f"sqlite:///{tmp_path / 'unit.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def make_app(tmp_path):
    """Build an application against a temporary database.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        A factory that accepts Settings field overrides.
    """

    def _make(**overrides):
        values = {
            "database_path": tmp_path / "app.db",
            "session_secret": "test-secret",
            "llm_api_key": "",
            "llm_base_url": "https://llm.example/v1",
            "llm_model": "test-model",
            "enable_face_compare": False,
            "seed_on_empty": False,
        }
        values.update(overrides)
        return create_app(Settings(**values))

    return _make


@pytest.fixture
def render_stub(monkeypatch):
    """Replace template rendering with the JSON test renderer.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr("app.routes._render", _json_render)


@pytest.fixture
def api(make_app, render_stub) -> Api:
    """Return a CSRF-aware client for the default test application.

    Args:
        make_app: Application factory.
        render_stub: Installed JSON renderer.

    Yields:
        An Api bound to a fresh database.
    """
    app = make_app()
    with TestClient(app, follow_redirects=False) as client:
        yield Api(client, app)


@pytest.fixture
def face_api(make_app, render_stub, tmp_path) -> Api:
    """Return a client with the resemblance flag enabled.

    Args:
        make_app: Application factory.
        render_stub: Installed JSON renderer.
        tmp_path: Pytest temporary directory.

    Yields:
        An Api whose settings enable face compare.
    """
    app = make_app(database_path=tmp_path / "face.db", enable_face_compare=True)
    with TestClient(app, follow_redirects=False) as client:
        yield Api(client, app)
