"""Donor physical traits: display formatting and couple-chosen filters.

Traits narrow the list a couple sees. They never change the medical rank.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.constants import (
    EYE_COLORS,
    HAIR_COLORS,
    HAIR_TYPES,
    HEIGHT_CM_MAX,
    HEIGHT_CM_MIN,
)
from app.models import Donor


@dataclass(frozen=True)
class TraitFilter:
    """Filters from the match page query string. Empty or None means any."""

    hair_color: str = ""
    hair_type: str = ""
    eye_color: str = ""
    min_height_cm: int | None = None
    max_height_cm: int | None = None
    max_bmi: float | None = None

    @property
    def active(self) -> bool:
        """True when at least one filter is set."""
        return any(
            (
                self.hair_color,
                self.hair_type,
                self.eye_color,
                self.min_height_cm is not None,
                self.max_height_cm is not None,
                self.max_bmi is not None,
            )
        )


def _option(params: Mapping[str, str], name: str, options: tuple[str, ...]) -> str:
    value = params.get(name, "").strip().lower()
    return value if value in options else ""


def _number(
    params: Mapping[str, str], name: str, low: float, high: float
) -> float | None:
    try:
        value = float(params.get(name, "").strip())
    except ValueError:
        return None
    return value if low <= value <= high else None


def parse_trait_filter(params: Mapping[str, str]) -> TraitFilter:
    """Read filters from query parameters. Unknown or out-of-range values are ignored.

    Args:
        params: Query string values.

    Returns:
        The filter the couple asked for.
    """
    min_height = _number(params, "min_height_cm", HEIGHT_CM_MIN, HEIGHT_CM_MAX)
    max_height = _number(params, "max_height_cm", HEIGHT_CM_MIN, HEIGHT_CM_MAX)
    return TraitFilter(
        hair_color=_option(params, "hair_color", HAIR_COLORS),
        hair_type=_option(params, "hair_type", HAIR_TYPES),
        eye_color=_option(params, "eye_color", EYE_COLORS),
        min_height_cm=None if min_height is None else int(min_height),
        max_height_cm=None if max_height is None else int(max_height),
        max_bmi=_number(params, "max_bmi", 10, 60),
    )


def donor_passes(donor: Donor, trait_filter: TraitFilter) -> bool:
    """Return True when the donor meets every active filter.

    A donor whose record leaves a filtered trait blank does not pass that filter.

    Args:
        donor: Donor record.
        trait_filter: Filters from the match page.

    Returns:
        True when the donor should stay on the list.
    """
    checks = (
        (trait_filter.hair_color, donor.hair_color),
        (trait_filter.hair_type, donor.hair_type),
        (trait_filter.eye_color, donor.eye_color),
    )
    if any(wanted and wanted != actual for wanted, actual in checks):
        return False
    if trait_filter.min_height_cm is not None and (
        donor.height_cm is None or donor.height_cm < trait_filter.min_height_cm
    ):
        return False
    if trait_filter.max_height_cm is not None and (
        donor.height_cm is None or donor.height_cm > trait_filter.max_height_cm
    ):
        return False
    bmi = donor.bmi
    if trait_filter.max_bmi is not None and (bmi is None or bmi > trait_filter.max_bmi):
        return False
    return True


def format_height(height_cm: int | None) -> str:
    """Height in centimeters with feet and inches, such as 180 cm (5'11")."""
    if not height_cm:
        return ""
    total_inches = round(height_cm / 2.54)
    feet, inches = divmod(total_inches, 12)
    return f"{height_cm} cm ({feet}'{inches}\")"


def format_weight(weight_kg: int | None) -> str:
    """Weight in kilograms with pounds, such as 75 kg (165 lb)."""
    if not weight_kg:
        return ""
    return f"{weight_kg} kg ({round(weight_kg * 2.20462)} lb)"


def trait_summary(donor: Donor) -> list[str]:
    """Short trait phrases for a match row. Blank fields are left out."""
    parts: list[str] = []
    hair = " ".join(item for item in (donor.hair_color, donor.hair_type) if item)
    if hair:
        parts.append(f"{hair.capitalize()} hair")
    if donor.eye_color:
        parts.append(f"{donor.eye_color} eyes")
    if donor.height_cm:
        parts.append(format_height(donor.height_cm))
    if donor.bmi is not None:
        parts.append(f"BMI {donor.bmi}")
    return parts
