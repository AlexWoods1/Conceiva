"""Turns a track table (track_id, frame, x, y, class) into per-sperm
motility metrics and a summary.json.
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

import config

CLUSTER_CLASS = 1


def classify_track(vcl, vsl):
    if vcl < config.IMMOTILE_VCL_MAX:
        return "immotile"
    if vsl >= config.PROGRESSIVE_VSL_MIN:
        return "progressive"
    return "non_progressive"


def compute_track_metrics(
    tracks_df,
    fps,
    micron_per_px=config.MICRONS_PER_PIXEL,
    min_track_seconds=config.MIN_TRACK_SECONDS,
):
    """One row per track: class, duration, VCL, VSL, LIN, motility category.

    Tracks shorter than min_track_seconds are dropped -- too little data to
    trust a velocity from (rule: short tracks are noise).
    """
    rows = []
    for track_id, g in tracks_df.groupby("track_id"):
        g = g.sort_values("frame")
        duration_sec = (g["frame"].iloc[-1] - g["frame"].iloc[0]) / fps
        if duration_sec < min_track_seconds:
            continue

        xs = g["x"].to_numpy() * micron_per_px
        ys = g["y"].to_numpy() * micron_per_px
        step_lengths = np.hypot(np.diff(xs), np.diff(ys))
        vcl = step_lengths.sum() / duration_sec
        vsl = np.hypot(xs[-1] - xs[0], ys[-1] - ys[0]) / duration_sec
        lin = vsl / vcl if vcl > 0 else 0.0

        rows.append(
            {
                "track_id": track_id,
                "class": int(g["class"].mode().iloc[0]),
                "duration_sec": duration_sec,
                "vcl": vcl,
                "vsl": vsl,
                "lin": lin,
                "category": classify_track(vcl, vsl),
            }
        )
    return pd.DataFrame(rows)


def build_summary(track_metrics, fps, micron_per_px=config.MICRONS_PER_PIXEL):
    is_cluster = track_metrics["class"] == CLUSTER_CLASS
    sperm = track_metrics[~is_cluster]
    cluster_count = int(is_cluster.sum())

    detection_class_counts = {
        config.CLASS_NAMES[cls_id]: int((track_metrics["class"] == cls_id).sum())
        for cls_id in config.CLASS_NAMES
    }

    n = len(sperm)
    category_counts = sperm["category"].value_counts()
    pct = lambda cat: (category_counts.get(cat, 0) / n * 100) if n else 0.0

    return {
        "fps": fps,
        "microns_per_pixel": micron_per_px,
        "scale_is_approximate": True,
        "num_tracks_analyzed": n,
        "cluster_count": cluster_count,
        "detection_class_counts": detection_class_counts,
        "percent_progressive": pct("progressive"),
        "percent_non_progressive": pct("non_progressive"),
        "percent_immotile": pct("immotile"),
        "total_motility_percent": pct("progressive") + pct("non_progressive"),
        "mean_vcl": float(sperm["vcl"].mean()) if n else 0.0,
        "mean_vsl": float(sperm["vsl"].mean()) if n else 0.0,
    }


def analyze_tracks(tracks_path):
    """Reads a track CSV (plus its .meta.json sidecar for fps) and returns
    the summary dict -- no file writing, callers decide what to do with it."""
    tracks_path = Path(tracks_path)
    meta_path = tracks_path.with_suffix(".meta.json")
    if not meta_path.exists():
        raise SystemExit(
            f"{meta_path} not found -- motility.py needs the fps that track.py "
            "writes alongside the track CSV."
        )
    fps = json.loads(meta_path.read_text())["fps"]

    tracks_df = pd.read_csv(tracks_path)
    track_metrics = compute_track_metrics(tracks_df, fps)
    return build_summary(track_metrics, fps)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tracks", required=True)
    parser.add_argument("--out", default="out/summary.json")
    args = parser.parse_args()

    summary = analyze_tracks(args.tracks)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
