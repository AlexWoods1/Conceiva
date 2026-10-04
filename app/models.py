"""SQLite tables for accounts, surveys, donors, counseling visits, and logs."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base."""


class User(Base):
    """A couple, sperm-bank, or genetic-counselor login."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16))
    consent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    stripe_checkout_session_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )


class CounselorProfile(Base):
    """Display name shown when a couple books a visit."""

    __tablename__ = "counselor_profiles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120), default="")


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
    photo_key: Mapped[str] = mapped_column(String(32), default="default")
    baby_photo_key: Mapped[str] = mapped_column(String(32), default="")
    panel: Mapped[str] = mapped_column(String(128), default="")
    cmv: Mapped[str] = mapped_column(String(16), default="unknown")
    quarantine: Mapped[str] = mapped_column(String(32), default="cleared")
    family_limit: Mapped[int] = mapped_column(Integer, default=1)
    id_release_policy: Mapped[str] = mapped_column(String(32), default="either")
    catalog_source_url: Mapped[str] = mapped_column(Text, default="")
    catalog_confirmed: Mapped[bool] = mapped_column(default=True)
    motility_total_pct: Mapped[float | None] = mapped_column(Float, default=None)
    motility_progressive_pct: Mapped[float | None] = mapped_column(Float, default=None)
    motility_video_url: Mapped[str] = mapped_column(String(255), default="")
    motility_below_reference: Mapped[bool] = mapped_column(default=False)
    motility_non_progressive_pct: Mapped[float | None] = mapped_column(
        Float, default=None
    )
    motility_immotile_pct: Mapped[float | None] = mapped_column(Float, default=None)
    motility_track_count: Mapped[int | None] = mapped_column(Integer, default=None)
    motility_sample_timing: Mapped[str] = mapped_column(String(16), default="")
    motility_review_status: Mapped[str] = mapped_column(String(32), default="")
    hair_color: Mapped[str] = mapped_column(String(16), default="")
    hair_type: Mapped[str] = mapped_column(String(16), default="")
    eye_color: Mapped[str] = mapped_column(String(16), default="")
    height_cm: Mapped[int | None] = mapped_column(Integer, default=None)
    weight_kg: Mapped[int | None] = mapped_column(Integer, default=None)
    ethnicity: Mapped[str] = mapped_column(String(64), default="")

    @property
    def bmi(self) -> float | None:
        """Body mass index from height and weight, or None when either is missing."""
        if not self.height_cm or not self.weight_kg:
            return None
        meters = self.height_cm / 100
        return round(self.weight_kg / (meters * meters), 1)


class MotilitySample(Base):
    """One microscope clip for a donor: pre-freeze, or the same sample after thaw."""

    __tablename__ = "motility_samples"
    __table_args__ = (
        UniqueConstraint("donor_id", "timing", name="uq_motility_sample_donor_timing"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    donor_id: Mapped[int] = mapped_column(ForeignKey("donors.id"), index=True)
    timing: Mapped[str] = mapped_column(String(16))
    total_pct: Mapped[float | None] = mapped_column(Float, default=None)
    progressive_pct: Mapped[float | None] = mapped_column(Float, default=None)
    non_progressive_pct: Mapped[float | None] = mapped_column(Float, default=None)
    immotile_pct: Mapped[float | None] = mapped_column(Float, default=None)
    track_count: Mapped[int | None] = mapped_column(Integer, default=None)
    video_url: Mapped[str] = mapped_column(String(255), default="")
    mean_vcl: Mapped[float | None] = mapped_column(Float, default=None)
    mean_vsl: Mapped[float | None] = mapped_column(Float, default=None)
    fps: Mapped[float | None] = mapped_column(Float, default=None)
    scale_is_approximate: Mapped[bool | None] = mapped_column(default=None)
    cluster_count: Mapped[int | None] = mapped_column(Integer, default=None)


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


class ShortlistItem(Base):
    """One donor a couple saved for counseling. Live list, capped in routes."""

    __tablename__ = "shortlist_items"
    __table_args__ = (
        UniqueConstraint(
            "couple_user_id", "donor_id", name="uq_shortlist_couple_donor"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    couple_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    donor_id: Mapped[int] = mapped_column(ForeignKey("donors.id"), index=True)


class AvailabilitySlot(Base):
    """A counselor's open or bookable time window."""

    __tablename__ = "availability_slots"

    id: Mapped[int] = mapped_column(primary_key=True)
    counselor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=45)


class Appointment(Base):
    """A booked counselor visit for one couple. Payment is out of scope."""

    __tablename__ = "appointments"

    id: Mapped[int] = mapped_column(primary_key=True)
    couple_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    counselor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    slot_id: Mapped[int] = mapped_column(
        ForeignKey("availability_slots.id"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="booked")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AppointmentDonor(Base):
    """Donor ids the couple booked with. Profiles stay live in the donor table."""

    __tablename__ = "appointment_donors"
    __table_args__ = (
        UniqueConstraint("appointment_id", "donor_id", name="uq_appointment_donor"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    appointment_id: Mapped[int] = mapped_column(
        ForeignKey("appointments.id"), index=True
    )
    donor_id: Mapped[int] = mapped_column(ForeignKey("donors.id"), index=True)


class CandidateReport(Base):
    """Counselor AI report for one visit candidate. Overwritten on re-run."""

    __tablename__ = "candidate_reports"
    __table_args__ = (
        UniqueConstraint("appointment_id", "donor_id", name="uq_candidate_report"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    appointment_id: Mapped[int] = mapped_column(
        ForeignKey("appointments.id"), index=True
    )
    donor_id: Mapped[int] = mapped_column(ForeignKey("donors.id"), index=True)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
