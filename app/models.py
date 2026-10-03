"""SQLite tables for accounts, surveys, donors, and explanation logs."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base."""


class User(Base):
    """A couple login or a sperm-bank enterprise login."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16))
    consent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class CoupleProfile(Base):
    """Blood type and the synthetic adult reference used when resemblance is on."""

    __tablename__ = "couple_profiles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    blood_type: Mapped[str] = mapped_column(String(8), default="")
    rh: Mapped[str] = mapped_column(String(16), default="")
    adult_photo_key: Mapped[str] = mapped_column(String(32), default="")


class PriorHistory(Base):
    """Previous pregnancies, donors, losses, and family conditions."""

    __tablename__ = "prior_histories"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    prior_pregnancies: Mapped[int] = mapped_column(Integer, default=0)
    prior_donors: Mapped[str] = mapped_column(Text, default="")
    miscarriages: Mapped[int] = mapped_column(Integer, default=0)
    known_conditions: Mapped[str] = mapped_column(Text, default="")


class CoupleSurvey(Base):
    """Two-step couple survey: clinical history, then preferences."""

    __tablename__ = "couple_surveys"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    ancestry: Mapped[str] = mapped_column(String(255), default="")
    cmv_requirement: Mapped[str] = mapped_column(String(32), default="any")
    clinical_notes: Mapped[str] = mapped_column(Text, default="")
    id_release: Mapped[str] = mapped_column(String(32), default="")
    family_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    clinical_done: Mapped[bool] = mapped_column(default=False)
    preferences_done: Mapped[bool] = mapped_column(default=False)


class Donor(Base):
    """A donor record owned by one sperm bank."""

    __tablename__ = "donors"

    id: Mapped[int] = mapped_column(primary_key=True)
    bank_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    code: Mapped[str] = mapped_column(String(64))
    blood_type: Mapped[str] = mapped_column(String(8), default="")
    rh: Mapped[str] = mapped_column(String(16), default="")
    ancestry: Mapped[str] = mapped_column(String(255), default="")
    photo_key: Mapped[str] = mapped_column(String(32), default="geo-1")
    panel: Mapped[str] = mapped_column(String(128), default="")
    cmv: Mapped[str] = mapped_column(String(16), default="unknown")
    quarantine: Mapped[str] = mapped_column(String(32), default="cleared")
    family_limit: Mapped[int] = mapped_column(Integer, default=1)
    id_release_policy: Mapped[str] = mapped_column(String(32), default="either")
    catalog_source_url: Mapped[str] = mapped_column(Text, default="")
    catalog_confirmed: Mapped[bool] = mapped_column(default=True)


class Carrier(Base):
    """One gene result on a couple or a donor."""

    __tablename__ = "carriers"

    id: Mapped[int] = mapped_column(primary_key=True)
    subject_type: Mapped[str] = mapped_column(String(16), index=True)
    subject_id: Mapped[int] = mapped_column(Integer, index=True)
    gene: Mapped[str] = mapped_column(String(32))
    zygosity: Mapped[str] = mapped_column(String(32))
    condition: Mapped[str] = mapped_column(String(255), default="")


class ContactMessage(Base):
    """A message from the public contact form. It is not tied to an account."""

    __tablename__ = "contact_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    email: Mapped[str] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LlmLog(Base):
    """Stored match explanation. Deleted with the account."""

    __tablename__ = "llm_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    donor_id: Mapped[int] = mapped_column(Integer, index=True)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
