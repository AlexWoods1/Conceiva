import numpy as np
import pandas as pd
import pytest

from motility import build_summary, classify_track, compute_track_metrics

FPS = 50
N_FRAMES = 30  # (N_FRAMES - 1) / FPS = 0.58s, clears MIN_TRACK_SECONDS (0.5s)


def _track(track_id, xs, ys, cls=0):
    return pd.DataFrame(
        {
            "track_id": track_id,
            "frame": range(len(xs)),
            "x": xs,
            "y": ys,
            "class": cls,
        }
    )


def still_point_track(track_id=1):
    return _track(track_id, [100.0] * N_FRAMES, [100.0] * N_FRAMES)


def straight_line_track(track_id=2):
    xs = [100.0 + 5 * i for i in range(N_FRAMES)]  # 5px/frame -> fast and straight
    return _track(track_id, xs, [100.0] * N_FRAMES)


def tight_circle_track(track_id=3, radius=3.0):
    angles = [2 * np.pi * i / N_FRAMES for i in range(N_FRAMES)]
    xs = [100 + radius * np.cos(a) for a in angles]
    ys = [100 + radius * np.sin(a) for a in angles]
    return _track(track_id, xs, ys)


def test_still_point_is_immotile():
    metrics = compute_track_metrics(still_point_track(), fps=FPS, micron_per_px=1)
    assert metrics.iloc[0]["vcl"] == 0
    assert metrics.iloc[0]["category"] == "immotile"


def test_straight_line_is_progressive():
    metrics = compute_track_metrics(straight_line_track(), fps=FPS, micron_per_px=1)
    row = metrics.iloc[0]
    assert row["vcl"] == pytest.approx(row["vsl"])  # straight path: VCL == VSL
    assert row["category"] == "progressive"


def test_tight_circle_is_non_progressive():
    metrics = compute_track_metrics(tight_circle_track(), fps=FPS, micron_per_px=1)
    row = metrics.iloc[0]
    assert row["vcl"] > 10  # clearly moving, not immotile
    assert row["vsl"] < row["vcl"]  # curvy path: barely any net displacement
    assert row["category"] == "non_progressive"


def test_classify_track_thresholds():
    assert classify_track(vcl=1, vsl=0) == "immotile"
    assert classify_track(vcl=50, vsl=10) == "progressive"
    assert classify_track(vcl=50, vsl=1) == "non_progressive"


def test_short_tracks_are_dropped():
    short = _track(1, [100.0, 101.0], [100.0, 100.0])  # 1 frame span, well under 0.5s
    metrics = compute_track_metrics(short, fps=FPS, micron_per_px=1)
    assert metrics.empty


def test_build_summary_excludes_clusters_and_sums_to_total_motility():
    tracks = pd.concat(
        [
            still_point_track(track_id=1),
            straight_line_track(track_id=2),
            tight_circle_track(track_id=3),
            _track(4, [100.0] * N_FRAMES, [100.0] * N_FRAMES, cls=1),  # cluster
        ]
    )
    metrics = compute_track_metrics(tracks, fps=FPS, micron_per_px=1)
    summary = build_summary(metrics, fps=FPS, micron_per_px=1)

    assert summary["cluster_count"] == 1
    assert summary["num_tracks_analyzed"] == 3  # cluster excluded
    assert summary["percent_progressive"] == pytest.approx(100 / 3)
    assert summary["percent_non_progressive"] == pytest.approx(100 / 3)
    assert summary["percent_immotile"] == pytest.approx(100 / 3)
    assert summary["total_motility_percent"] == pytest.approx(
        summary["percent_progressive"] + summary["percent_non_progressive"]
    )
