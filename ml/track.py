"""Runs detection + ByteTrack on a video, writes a per-frame track table,
and renders an annotated clip with ID labels and motion trails.
"""

import argparse
import json
import subprocess
from collections import defaultdict, deque
from pathlib import Path

import cv2
import numpy as np
import pandas as pd
from ultralytics import YOLO

import config
from motility import classify_track

# BGR. A track is drawn in its running motility category's color, so a viewer
# can see which sperm are moving well and which are not as the clip plays.
CATEGORY_COLORS = {
    "progressive": (0, 220, 0),
    "non_progressive": (0, 220, 220),
    "immotile": (0, 0, 230),
}
UNCLASSIFIED_COLOR = (170, 170, 170)


def running_category(points, fps):
    """Motility category from a track's points so far, or None when the track
    is still too short to judge.

    This is a live approximation: it reclassifies on each frame from the
    history available at that moment, so early in a track the category can
    change. The summary numbers are computed separately in motility.py from
    whole tracks, and those are the ones reported.
    """
    if len(points) < 2:
        return None
    duration_sec = (points[-1][0] - points[0][0]) / fps
    if duration_sec < config.MIN_TRACK_SECONDS:
        return None
    xs = np.array([p[1] for p in points]) * config.MICRONS_PER_PIXEL
    ys = np.array([p[2] for p in points]) * config.MICRONS_PER_PIXEL
    vcl = np.hypot(np.diff(xs), np.diff(ys)).sum() / duration_sec
    vsl = np.hypot(xs[-1] - xs[0], ys[-1] - ys[0]) / duration_sec
    return classify_track(vcl, vsl)


def _require_local_video_file(video_path):
    """Rejects anything ultralytics would treat as a URL/stream instead of a
    local file -- model.track(source=...) fetches http(s)/rtsp/rtmp sources
    server-side, which is an SSRF vector if video_path ever traces back to
    unvalidated input."""
    path = Path(video_path)
    if "://" in str(video_path) or not path.is_file():
        raise ValueError(
            f"video_path must be an existing local file, got: {video_path!r}"
        )
    return path


LEGEND = [
    ("progressive", "progressive"),
    ("non_progressive", "non-progressive"),
    ("immotile", "immotile"),
]


def _draw_legend(img):
    """Color key burned into the clip, so the overlay reads on its own."""
    for row, (category, label) in enumerate(LEGEND):
        y = 15 + row * 16
        cv2.circle(img, (12, y - 4), 4, CATEGORY_COLORS[category], -1)
        cv2.putText(
            img,
            label,
            (24, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            CATEGORY_COLORS[category],
            1,
        )


def run_tracker(video_path, model):
    """Yields (frame_idx, track_id, class_id, x_center, y_center, orig_img) per detection."""
    for frame_idx, result in enumerate(
        model.track(
            source=video_path,
            tracker=config.TRACKER_CONFIG,
            conf=config.TRACK_CONF_THRESHOLD,
            persist=True,
            stream=True,
            verbose=False,
        )
    ):
        boxes = result.boxes
        if boxes.id is None:
            yield frame_idx, [], result.orig_img
            continue
        track_ids = boxes.id.int().tolist()
        class_ids = boxes.cls.int().tolist()
        centers = boxes.xywh[:, :2].tolist()
        detections = [
            (track_id, cls_id, x, y)
            for track_id, cls_id, (x, y) in zip(track_ids, class_ids, centers)
        ]
        yield frame_idx, detections, result.orig_img


def track_video(video_path, out_csv, out_video, model=None):
    """Runs detection+tracking on video_path, writes the track table to
    out_csv (plus an out_csv.meta.json sidecar with fps/width/height) and an
    annotated H.264 clip to out_video. Pass a pre-loaded model to avoid
    reloading weights on every call (e.g. from a long-lived server process).
    """
    out_csv = Path(out_csv)
    out_video = Path(out_video)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out_video.parent.mkdir(parents=True, exist_ok=True)

    video_path = _require_local_video_file(video_path)
    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()

    if model is None:
        model = YOLO(config.BEST_WEIGHTS_PATH)

    raw_video = out_video.with_suffix(".raw.mp4")
    writer = cv2.VideoWriter(
        str(raw_video), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height)
    )

    trails = defaultdict(lambda: deque(maxlen=config.TRAIL_LENGTH))
    full_history = defaultdict(list)  # unbounded, the trail deque is display-only
    categories = {}
    rows = []

    for frame_idx, detections, img in run_tracker(str(video_path), model):
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
            trails[track_id].append((int(x), int(y)))
            full_history[track_id].append((frame_idx, x, y))
            categories[track_id] = running_category(full_history[track_id], fps)

        for track_id, points in trails.items():
            color = CATEGORY_COLORS.get(categories.get(track_id), UNCLASSIFIED_COLOR)
            for p1, p2 in zip(points, list(points)[1:]):
                cv2.line(img, p1, p2, color, 1)

        for track_id, cls_id, x, y in detections:
            color = CATEGORY_COLORS.get(categories.get(track_id), UNCLASSIFIED_COLOR)
            cv2.circle(img, (int(x), int(y)), 3, color, -1)
            cv2.putText(
                img,
                str(track_id),
                (int(x) + 5, int(y) - 5),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.4,
                color,
                1,
            )

        _draw_legend(img)
        writer.write(img)

    writer.release()

    pd.DataFrame(rows, columns=["track_id", "frame", "x", "y", "class"]).to_csv(
        out_csv, index=False
    )
    out_csv.with_suffix(".meta.json").write_text(
        json.dumps({"fps": fps, "width": width, "height": height})
    )
    print(f"wrote {len(rows)} detections across tracks to {out_csv}")

    # mp4v (OpenCV's default) doesn't play in browsers, re-encode to H.264.
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(raw_video),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(out_video),
        ],
        check=True,
        capture_output=True,
    )
    raw_video.unlink()
    print(f"wrote annotated clip to {out_video}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--out", default="out/tracks.csv")
    parser.add_argument("--out-video", default="out/tracked.mp4")
    args = parser.parse_args()
    track_video(args.video, args.out, args.out_video)


if __name__ == "__main__":
    main()
