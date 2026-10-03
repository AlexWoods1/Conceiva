"""Application factory."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.config import Settings, load_settings
from app.models import Base
from app.routes import _RedirectNeeded, router
from app.seed import seed_demo

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


class DatabaseMiddleware(BaseHTTPMiddleware):
    """Open one database session per request and commit when the handler succeeds."""

    async def dispatch(self, request: Request, call_next):
        factory = request.app.state.session_factory
        session = factory()
        request.state.db = session
        try:
            response = await call_next(request)
        except Exception:
            session.rollback()
            raise
        else:
            if response.status_code < 400:
                session.commit()
            else:
                session.rollback()
            return response
        finally:
            session.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the site.

    Args:
        settings: Explicit settings for tests. The process environment is used
            when this is omitted.

    Returns:
        A FastAPI application.
    """
    settings = settings or load_settings()
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    existed = settings.database_path.exists()
    engine = create_engine(
        f"sqlite:///{settings.database_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    if settings.seed_on_empty and not existed:
        session = factory()
        try:
            seed_demo(session)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    app = FastAPI(title="Donor Match")
    app.state.settings = settings
    app.state.session_factory = factory
    app.add_middleware(DatabaseMiddleware)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        same_site="lax",
        https_only=settings.secure_cookies,
    )
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(router)

    @app.exception_handler(_RedirectNeeded)
    async def _redirect_guard(_request: Request, exc: _RedirectNeeded):
        return RedirectResponse(exc.path, status_code=303)

    return app


def _runtime_app() -> FastAPI:
    """Return the process app, skipping demo startup while pytest is importing."""
    if "pytest" in sys.modules:
        return FastAPI()
    return create_app()


# * Vercel loads a FastAPI instance named app from app/main.py.
app = _runtime_app()
