"""Thin inference entrypoint for embedding in another webapp: one function,
video in, motility results + an annotated clip out. No HTTP server here --
the caller's own backend imports and calls this directly.
"""

import sys
from pathlib import Path

ML_DIR = Path(__file__).resolve().parent.parent / "ml"
sys.path.insert(0, str(ML_DIR))

import config  # noqa: E402  (ml/config.py, path added above)
from motility import analyze_tracks  # noqa: E402
from track import track_video  # noqa: E402
from ultralytics import YOLO  # noqa: E402

DISCLAIMER = (
    "Research and education demo only. Not a diagnostic tool and does not "
    "replace a clinical semen analysis."
)

_model = None


def get_model():
    """Loads the trained detector once per process, reused across calls."""
    global _model
    if _model is None:
        _model = YOLO(config.BEST_WEIGHTS_PATH)
    return _model


def analyze_video(video_path, out_dir="out"):
    """Runs detection -> tracking -> motility metrics on one video.

    Returns:
        {
            "summary": {... see ml/motility.py build_summary() for fields ...},
            "annotated_video_path": str, path to an H.264 clip with ID labels and motion trails,
            "who_reference": {"progressive_motility_min_pct": ..., "total_motility_min_pct": ...},
            "disclaimer": str, must be shown alongside any result in a UI,
        }
    """
    out_dir = Path(out_dir)
    tracks_csv = out_dir / "tracks.csv"
    annotated_video = out_dir / "tracked.mp4"

    track_video(video_path, tracks_csv, annotated_video, model=get_model())
    summary = analyze_tracks(tracks_csv)

    return {
        "summary": summary,
        "annotated_video_path": str(annotated_video),
        "who_reference": {
            "progressive_motility_min_pct": config.WHO_PROGRESSIVE_MOTILITY_MIN_PCT,
            "total_motility_min_pct": config.WHO_TOTAL_MOTILITY_MIN_PCT,
        },
        "disclaimer": DISCLAIMER,
    }
