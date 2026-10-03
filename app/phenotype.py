"""Non-clinical resemblance stub.

This does not predict a child and does not call a face API. Demo portraits are
synthetic drawings. The medical rank never includes this number.
"""

from __future__ import annotations

import hashlib


def resemblance_score(donor_key: str, adult_key: str, enabled: bool) -> int | None:
    """Return a stable stub score for two synthetic portrait keys.

    Args:
        donor_key: Synthetic childhood portrait key.
        adult_key: Synthetic adult reference key.
        enabled: ENABLE_FACE_COMPARE. When false, the score is absent.

    Returns:
        An integer from 35 to 80, or None when the flag is off or a key is missing.
    """
    if not enabled or not donor_key or not adult_key:
        return None
    digest = hashlib.sha256(f"{donor_key}:{adult_key}".encode("utf-8")).digest()
    return 35 + digest[0] % 46
