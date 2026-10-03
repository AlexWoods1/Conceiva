"""Shared enumerations for forms and the scorer."""

BLOOD_TYPES = ("O", "A", "B", "AB")
RH_VALUES = ("positive", "negative")
ZYGOSITIES = ("heterozygous", "homozygous", "unknown")
ZYGOSITY_LABELS = {
    "heterozygous": "One altered copy (carrier)",
    "homozygous": "Two altered copies",
    "unknown": "The report does not say",
}
# * Common recessive results. The rank compares the gene code, not the condition name.
COMMON_CARRIERS = (
    ("CFTR", "Cystic fibrosis"),
    ("HBB", "Sickle cell disease"),
    ("HEXA", "Tay-Sachs disease"),
    ("SMN1", "Spinal muscular atrophy"),
    ("GBA", "Gaucher disease"),
    ("ASPA", "Canavan disease"),
)
CONFIRMED_CARRIER = ("heterozygous", "homozygous")
ID_RELEASE = ("open", "anonymous", "either")
CMV_STATUS = ("negative", "positive", "unknown")
CMV_REQUIREMENTS = ("any", "negative_required")
QUARANTINE = ("cleared", "in_quarantine")
# * default is the new-donor placeholder; geo-* remain optional DiceBear stand-ins.
PHOTO_KEYS = ("default", "geo-1", "geo-2", "geo-3", "geo-4", "geo-5", "geo-6")
ADULT_PHOTO_KEYS = ("adult-1", "adult-2")
# * The donor's own childhood photo, as banks publish them. Never a child prediction.
BABY_PHOTO_KEYS = ("baby-1", "baby-2", "baby-3", "baby-4", "baby-5", "baby-6")
ROLES = ("couple", "bank", "counselor")
# * Cap so a counselor can discuss each candidate in one visit.
MAX_SHORTLIST = 20
DEFAULT_SLOT_MINUTES = 45
APPOINTMENT_BOOKED = "booked"
APPOINTMENT_CANCELLED = "cancelled"

RH_LABELS = {
    "positive": "Rh positive",
    "negative": "Rh negative",
}
CMV_REQUIREMENT_LABELS = {
    "any": "Any CMV status",
    "negative_required": "Need a CMV-negative donor",
}
ID_RELEASE_LABELS = {
    "open": "Open ID, the donor can be known later",
    "anonymous": "Anonymous",
    "either": "Either is fine",
}
CMV_STATUS_LABELS = {
    "negative": "CMV negative",
    "positive": "CMV positive",
    "unknown": "CMV not recorded",
}
QUARANTINE_LABELS = {
    "cleared": "Cleared",
    "in_quarantine": "Still in quarantine",
}

# * Physical traits a bank records. Shown and filterable, never part of the rank.
HAIR_COLORS = ("black", "brown", "blond", "red", "gray")
HAIR_TYPES = ("straight", "wavy", "curly", "coily")
EYE_COLORS = ("brown", "hazel", "green", "blue", "gray")
HEIGHT_CM_MIN = 140
HEIGHT_CM_MAX = 220
WEIGHT_KG_MIN = 40
WEIGHT_KG_MAX = 160

# * Soft penalties. A shared recessive carrier is a hard stop, not a penalty.
FAMILY_LIMIT_PENALTY = 20
ID_RELEASE_PENALTY = 15
CMV_PENALTY = 15
CMV_UNKNOWN_PENALTY = 8
QUARANTINE_PENALTY = 25
# * Motility below the WHO lower reference limits. A soft penalty, like
# * quarantine -- a research-demo measurement must not hard-stop a donor.
MOTILITY_PENALTY = 20

# * WHO laboratory manual, 6th edition, lower reference limits -- for display
# * alongside a motility result, not used in matching.
WHO_PROGRESSIVE_MOTILITY_MIN_PCT = 30
WHO_TOTAL_MOTILITY_MIN_PCT = 42
MAX_MOTILITY_UPLOAD_BYTES = 200 * 1024 * 1024  # a 30s microscopy clip is a few MB
MOTILITY_DISCLAIMER = (
    "Research and education demo only. Not a diagnostic tool and does not "
    "replace a clinical semen analysis."
)
