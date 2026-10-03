"""Compares predicted motility against lab-measured ground truth.

Rule: tune thresholds only on training videos, evaluate once on the 4
held-out videos. --split defaults to test (the held-out set) since that's
the one official evaluation; pass --split train while tuning, and add
--tune to grid-search PROGRESSIVE_VSL_MIN / IMMOTILE_VCL_MAX instead of just
scoring the current config.py values (still prints results only -- update
config.py by hand with whatever the sweep finds).
"""

import argparse
import json
from pathlib import Path

import cv2
import matplotlib.pyplot as plt
import pandas as pd
from ultralytics import YOLO

import config
from motility import build_summary, classify_track, compute_track_metrics
from track import run_tracker

IMMOTILE_VCL_MAX_CANDIDATES = [2, 5, 8, 10, 15, 20, 25, 30, 40, 50]
PROGRESSIVE_VSL_MIN_CANDIDATES = [3, 5, 8, 10, 15, 20, 25, 30]


def get_tracks_df(model, video_id):
    """Runs detection + tracking once; the expensive part, cache the result
    per video_id yourself if sweeping thresholds across many combos."""
    video_path = config.RAW_DATA_DIR / video_id / f"{video_id}.mp4"
    rows = []
    for frame_idx, detections, _ in run_tracker(str(video_path), model):
        for track_id, cls_id, x, y in detections:
            rows.append(
                {
                    "track_id": track_id,
                    "frame": frame_idx,
                    "x": x,
                    "y": y,
                    "class": cls_id,
                }
            )

    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()

    return pd.DataFrame(rows, columns=["track_id", "frame", "x", "y", "class"]), fps


def load_lab_ground_truth():
    csv_path = config.RAW_DATA_DIR.parent.parent / "semen_analysis_data_Train.csv"
    return pd.read_csv(csv_path).set_index("ID")


def lab_total_motility(lab_row):
    return (
        lab_row["Progressive motility (%)"]
        + lab_row["Non progressive sperm motility (%)"]
    )


def evaluate_split(model, video_ids, lab, out_path, split_name):
    results = []
    for video_id in video_ids:
        pid = int(video_id)
        if pid not in lab.index:
            print(f"skipping {video_id}: no lab ground truth row")
            continue
        tracks_df, fps = get_tracks_df(model, video_id)
        track_metrics = compute_track_metrics(tracks_df, fps)
        pred = build_summary(track_metrics, fps)
        lab_row = lab.loc[pid]
        results.append(
            {
                "video_id": video_id,
                "pred_progressive": pred["percent_progressive"],
                "lab_progressive": lab_row["Progressive motility (%)"],
                "pred_total": pred["total_motility_percent"],
                "lab_total": lab_total_motility(lab_row),
            }
        )
        print(
            f"{video_id}: pred_total={pred['total_motility_percent']:.1f}  lab_total={results[-1]['lab_total']:.1f}"
        )

    results_df = pd.DataFrame(results)
    mae_progressive = (
        (results_df["pred_progressive"] - results_df["lab_progressive"]).abs().mean()
    )
    mae_total = (results_df["pred_total"] - results_df["lab_total"]).abs().mean()

    fig, axes = plt.subplots(1, 2, figsize=(10, 5))
    for ax, pred_col, lab_col, mae, title in [
        (
            axes[0],
            "pred_progressive",
            "lab_progressive",
            mae_progressive,
            "Progressive motility (%)",
        ),
        (axes[1], "pred_total", "lab_total", mae_total, "Total motility (%)"),
    ]:
        ax.scatter(results_df[lab_col], results_df[pred_col])
        for _, row in results_df.iterrows():
            ax.annotate(row["video_id"], (row[lab_col], row[pred_col]), fontsize=8)
        lims = [0, 100]
        ax.plot(lims, lims, "--", color="gray", label="perfect agreement")
        ax.set_xlabel(f"lab-measured {title}")
        ax.set_ylabel(f"predicted {title}")
        ax.set_title(
            f"{title}\nMAE = {mae:.1f} pts  (n={len(results_df)}, {split_name} split)"
        )
        ax.set_xlim(lims)
        ax.set_ylim(lims)
        ax.legend()

    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    print(f"\nMAE progressive: {mae_progressive:.2f} points")
    print(f"MAE total motility: {mae_total:.2f} points")
    print(f"wrote {out_path}")


def tune_thresholds(model, video_ids, lab):
    """Grid search over the two classification thresholds, scored by MAE
    against lab ground truth. Detection+tracking runs once per video; the
    sweep itself just reclassifies already-computed VCL/VSL per track."""
    cached = {}
    for video_id in video_ids:
        pid = int(video_id)
        if pid not in lab.index:
            continue
        tracks_df, fps = get_tracks_df(model, video_id)
        cached[video_id] = (compute_track_metrics(tracks_df, fps), lab.loc[pid])

    results = []
    for immotile_vcl_max in IMMOTILE_VCL_MAX_CANDIDATES:
        for progressive_vsl_min in PROGRESSIVE_VSL_MIN_CANDIDATES:
            config.IMMOTILE_VCL_MAX = immotile_vcl_max
            config.PROGRESSIVE_VSL_MIN = progressive_vsl_min
            total_errors, prog_errors = [], []
            for metrics, lab_row in cached.values():
                is_cluster = metrics["class"] == 1
                sperm = metrics[~is_cluster].copy()
                sperm["category"] = sperm.apply(
                    lambda r: classify_track(r["vcl"], r["vsl"]), axis=1
                )
                n = len(sperm)
                pct_progressive = (
                    (sperm["category"] == "progressive").sum() / n * 100 if n else 0.0
                )
                pct_total = (
                    (sperm["category"] != "immotile").sum() / n * 100 if n else 0.0
                )
                total_errors.append(abs(pct_total - lab_total_motility(lab_row)))
                prog_errors.append(
                    abs(pct_progressive - lab_row["Progressive motility (%)"])
                )
            mae_total = sum(total_errors) / len(total_errors)
            mae_prog = sum(prog_errors) / len(prog_errors)
            results.append(
                (
                    immotile_vcl_max,
                    progressive_vsl_min,
                    mae_total,
                    mae_prog,
                    mae_total + mae_prog,
                )
            )

    results.sort(key=lambda r: r[4])
    print(
        f"{'IMMOTILE_VCL_MAX':>18} {'PROGRESSIVE_VSL_MIN':>20} {'MAE total':>10} {'MAE prog':>10}"
    )
    for immotile_vcl_max, progressive_vsl_min, mae_total, mae_prog, _ in results[:10]:
        print(
            f"{immotile_vcl_max:>18} {progressive_vsl_min:>20} {mae_total:>10.2f} {mae_prog:>10.2f}"
        )
    best = results[0]
    print(
        f"\nbest: IMMOTILE_VCL_MAX={best[0]}, PROGRESSIVE_VSL_MIN={best[1]} "
        f"(MAE total={best[2]:.2f}, MAE progressive={best[3]:.2f}) -- update config.py by hand"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=["train", "test"], default="test")
    parser.add_argument("--out", default="out/evaluation.png")
    parser.add_argument(
        "--tune",
        action="store_true",
        help="grid-search thresholds instead of scoring config.py as-is",
    )
    args = parser.parse_args()

    split = json.loads(config.DATASET_SPLIT_FILE.read_text())
    video_ids = split[args.split]
    lab = load_lab_ground_truth()
    model = YOLO(config.BEST_WEIGHTS_PATH)

    if args.tune:
        tune_thresholds(model, video_ids, lab)
    else:
        evaluate_split(model, video_ids, lab, Path(args.out), args.split)


if __name__ == "__main__":
    main()
