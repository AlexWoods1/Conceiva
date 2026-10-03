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

# * SQLite create_all does not add columns to existing tables.
_DONOR_COLUMN_DDL = (
    ("motility_total_pct", "FLOAT"),
    ("motility_progressive_pct", "FLOAT"),
    ("motility_video_url", "VARCHAR(255) DEFAULT ''"),
)


def _ensure_sqlite_columns(engine) -> None:
    """Add missing donor columns after model changes on an existing SQLite file."""
    with engine.begin() as conn:
        existing = {
            row[1] for row in conn.exec_driver_sql("PRAGMA table_info(donors)").fetchall()
        }
        if not existing:
            return
        for name, sql_type in _DONOR_COLUMN_DDL:
            if name not in existing:
                conn.exec_driver_sql(
                    f"ALTER TABLE donors ADD COLUMN {name} {sql_type}"
                )


def _appointments_slot_id_is_unique(conn) -> bool:
    """Return True when appointments.slot_id still has a UNIQUE index."""
    tables = conn.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='appointments'"
    ).fetchall()
    if not tables:
        return False
    for index in conn.exec_driver_sql("PRAGMA index_list(appointments)").fetchall():
        # * PRAGMA index_list: seq, name, unique, origin, partial
        if not index[2]:
            continue
        columns = [
            row[2]
            for row in conn.exec_driver_sql(
                f"PRAGMA index_info('{index[1]}')"
            ).fetchall()
        ]
        if columns == ["slot_id"]:
            return True
    return False


def _ensure_appointments_slot_reusable(engine) -> None:
    """Drop UNIQUE(slot_id) so a cancelled visit can free a slot for rebooking.

    SQLite create_all does not rewrite an existing appointments table.
    """
    with engine.begin() as conn:
        if not _appointments_slot_id_is_unique(conn):
            return
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        conn.exec_driver_sql(
            """
            CREATE TABLE appointments_new (
                id INTEGER NOT NULL PRIMARY KEY,
                couple_user_id INTEGER NOT NULL,
                counselor_user_id INTEGER NOT NULL,
                slot_id INTEGER NOT NULL,
                status VARCHAR(32) NOT NULL,
                created_at DATETIME NOT NULL,
                FOREIGN KEY(couple_user_id) REFERENCES users (id),
                FOREIGN KEY(counselor_user_id) REFERENCES users (id),
                FOREIGN KEY(slot_id) REFERENCES availability_slots (id)
            )
            """
        )
        conn.exec_driver_sql(
            """
            INSERT INTO appointments_new
            (id, couple_user_id, counselor_user_id, slot_id, status, created_at)
            SELECT id, couple_user_id, counselor_user_id, slot_id, status, created_at
            FROM appointments
            """
        )
        conn.exec_driver_sql("DROP TABLE appointments")
        conn.exec_driver_sql("ALTER TABLE appointments_new RENAME TO appointments")
        conn.exec_driver_sql(
            "CREATE INDEX ix_appointments_couple_user_id ON appointments (couple_user_id)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX ix_appointments_counselor_user_id "
            "ON appointments (counselor_user_id)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX ix_appointments_slot_id ON appointments (slot_id)"
        )
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")


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
    _ensure_sqlite_columns(engine)
    _ensure_appointments_slot_reusable(engine)
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
