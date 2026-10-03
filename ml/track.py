"""Runs detection + ByteTrack on a video, writes a per-frame track table,
and renders an annotated clip with ID labels and motion trails.
"""

import argparse
import json
import subprocess
from collections import defaultdict, deque
from pathlib import Path

import cv2
import pandas as pd
from ultralytics import YOLO

import config

TRAIL_COLOR = (0, 215, 255)
BOX_COLOR = (0, 255, 0)


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

        for track_id, points in trails.items():
            for p1, p2 in zip(points, list(points)[1:]):
                cv2.line(img, p1, p2, TRAIL_COLOR, 1)

        for track_id, cls_id, x, y in detections:
            cv2.circle(img, (int(x), int(y)), 3, BOX_COLOR, -1)
            cv2.putText(
                img,
                str(track_id),
                (int(x) + 5, int(y) - 5),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.4,
                BOX_COLOR,
                1,
            )

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
