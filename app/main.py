"""Application factory."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.config import Settings, load_settings
from app.models import Base
from app.routes import _RedirectNeeded, router
from app.seed import seed_demo

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# * SQLite create_all does not add columns to existing tables.
_SQLITE_COLUMN_DDL: dict[str, tuple[tuple[str, str], ...]] = {
    "donors": (
        ("motility_total_pct", "FLOAT"),
        ("motility_progressive_pct", "FLOAT"),
        ("motility_video_url", "VARCHAR(255) DEFAULT ''"),
        ("motility_below_reference", "BOOLEAN DEFAULT 0"),
        ("motility_non_progressive_pct", "FLOAT"),
        ("motility_immotile_pct", "FLOAT"),
        ("motility_track_count", "INTEGER"),
        ("motility_sample_timing", "VARCHAR(16) NOT NULL DEFAULT ''"),
        ("motility_review_status", "VARCHAR(32) NOT NULL DEFAULT ''"),
        ("hair_color", "VARCHAR(16) NOT NULL DEFAULT ''"),
        ("hair_type", "VARCHAR(16) NOT NULL DEFAULT ''"),
        ("eye_color", "VARCHAR(16) NOT NULL DEFAULT ''"),
        ("height_cm", "INTEGER"),
        ("weight_kg", "INTEGER"),
        ("ethnicity", "VARCHAR(64) NOT NULL DEFAULT ''"),
        ("baby_photo_key", "VARCHAR(32) NOT NULL DEFAULT ''"),
    ),
    "motility_samples": (
        ("mean_vcl", "FLOAT"),
        ("mean_vsl", "FLOAT"),
        ("fps", "FLOAT"),
        ("scale_is_approximate", "BOOLEAN"),
        ("cluster_count", "INTEGER"),
    ),
    "users": (
        ("paid_at", "DATETIME"),
        ("stripe_checkout_session_id", "VARCHAR(255)"),
    ),
    "appointment_donors": (("donor_code", "VARCHAR(64) NOT NULL DEFAULT ''"),),
}


def _ensure_sqlite_columns(engine) -> None:
    """Add missing columns after model changes on an existing SQLite file."""
    with engine.begin() as conn:
        for table, columns in _SQLITE_COLUMN_DDL.items():
            existing = {
                row[1]
                for row in conn.exec_driver_sql(
                    f"PRAGMA table_info({table})"
                ).fetchall()
            }
            if not existing:
                continue
            for name, sql_type in columns:
                if name not in existing:
                    conn.exec_driver_sql(
                        f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"
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
        conn.exec_driver_sql("""
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
            """)
        conn.exec_driver_sql("""
            INSERT INTO appointments_new
            (id, couple_user_id, counselor_user_id, slot_id, status, created_at)
            SELECT id, couple_user_id, counselor_user_id, slot_id, status, created_at
            FROM appointments
            """)
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


def _ensure_one_booked_visit_per_couple(engine) -> None:
    """Partial unique index: at most one booked appointment per couple.

    Double-submits and races otherwise create duplicate visits or 500 on
    IntegrityError. Cancelled rows are excluded so rebooking stays allowed.
    """
    with engine.begin() as conn:
        # * Drop extras so CREATE UNIQUE INDEX can succeed on dirty demo DBs.
        dupes = conn.exec_driver_sql("""
            SELECT id FROM appointments
            WHERE status = 'booked'
              AND id NOT IN (
                SELECT MIN(id) FROM appointments
                WHERE status = 'booked'
                GROUP BY couple_user_id
              )
            """).fetchall()
        for (appointment_id,) in dupes:
            aid = int(appointment_id)
            conn.exec_driver_sql(
                f"DELETE FROM appointment_donors WHERE appointment_id = {aid}"
            )
            conn.exec_driver_sql(
                f"DELETE FROM candidate_reports WHERE appointment_id = {aid}"
            )
            conn.exec_driver_sql(f"DELETE FROM appointments WHERE id = {aid}")
        conn.exec_driver_sql("""
            CREATE UNIQUE INDEX IF NOT EXISTS
            uq_appointments_one_booked_per_couple
            ON appointments (couple_user_id)
            WHERE status = 'booked'
            """)


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
    engine = create_engine(
        f"sqlite:///{settings.database_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    _ensure_sqlite_columns(engine)
    _ensure_appointments_slot_reusable(engine)
    _ensure_one_booked_visit_per_couple(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    # * Idempotent seed: fills missing demo accounts without wiping existing rows.
    if settings.seed_on_empty:
        session = factory()
        try:
            seed_demo(session)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    app = FastAPI(title="Conceiva")
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
