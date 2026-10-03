"""Converts VISEM-Tracking into a YOLO dataset, split by whole video.

Layout found in data/VISEM_Tracking/VISEM_Tracking_Train_v4/Train/<id>/:
  <id>.mp4          full 30s video
  images/<id>_frame_<n>.jpg   pre-extracted frames, one per video frame
  labels/<id>_frame_<n>.txt   YOLO boxes: "<class> <xc> <yc> <w> <h>" (normalized)
  labels_ftid/...txt         same boxes plus a per-sperm tracking id column
                             (not used here, only needed for tracking eval)

We use labels/ (plain YOLO format) to build a standard YOLO detection dataset.
"""

import argparse
import json
import random
import shutil
from pathlib import Path

import cv2

import config


def discover_videos():
    """Every patient id folder under RAW_DATA_DIR that has images+labels."""
    ids = []
    for p in sorted(config.RAW_DATA_DIR.iterdir()):
        if p.is_dir() and (p / "images").is_dir() and (p / "labels").is_dir():
            ids.append(p.name)
    return ids


def load_or_create_split(video_ids):
    if config.DATASET_SPLIT_FILE.exists():
        split = json.loads(config.DATASET_SPLIT_FILE.read_text())
        known = set(split["train"]) | set(split["test"])
        if known != set(video_ids):
            raise ValueError(
                f"{config.DATASET_SPLIT_FILE} does not match the videos on disk. "
                "Delete it only if you mean to re-split (this breaks reproducibility)."
            )
        return split

    rng = random.Random(config.SPLIT_SEED)
    shuffled = video_ids[:]
    rng.shuffle(shuffled)
    test = sorted(shuffled[: config.NUM_TEST_VIDEOS])
    train = sorted(v for v in video_ids if v not in test)
    split = {"train": train, "test": test}
    config.DATASET_SPLIT_FILE.write_text(json.dumps(split, indent=2))
    return split


def frame_number(label_file):
    return int(label_file.stem.rsplit("_frame_", 1)[1])


def populate_split(video_ids, split_name, dataset_dir, stride=1):
    images_dir = dataset_dir / "images" / split_name
    labels_dir = dataset_dir / "labels" / split_name
    images_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)

    n_images = 0
    for vid in video_ids:
        src = config.RAW_DATA_DIR / vid
        label_files = sorted(
            (src / "labels").glob(f"{vid}_frame_*.txt"), key=frame_number
        )
        for label_file in label_files[::stride]:
            image_file = src / "images" / f"{label_file.stem}.jpg"
            if not image_file.exists():
                continue
            shutil.copy(image_file, images_dir / image_file.name)
            shutil.copy(label_file, labels_dir / label_file.name)
            n_images += 1
    return n_images


def write_data_yaml(dataset_dir):
    lines = [
        f"path: {dataset_dir}",
        "train: images/train",
        "val: images/test",
        "names:",
    ]
    for class_id, name in sorted(config.CLASS_NAMES.items()):
        lines.append(f"  {class_id}: {name}")
    (dataset_dir / "data.yaml").write_text("\n".join(lines) + "\n")


def draw_sample_frames(video_ids, out_dir, n=3):
    """Render a few frames with ground-truth boxes for a visual sanity check."""
    out_dir.mkdir(parents=True, exist_ok=True)
    colors = {0: (0, 255, 0), 1: (0, 0, 255), 2: (255, 255, 0)}

    for vid in video_ids[:n]:
        src = config.RAW_DATA_DIR / vid
        label_file = sorted((src / "labels").glob(f"{vid}_frame_*.txt"))[100]
        image_file = src / "images" / f"{label_file.stem}.jpg"
        img = cv2.imread(str(image_file))
        h, w = img.shape[:2]

        for line in label_file.read_text().splitlines():
            cls, xc, yc, bw, bh = line.split()
            cls = int(cls)
            xc, yc, bw, bh = (float(v) for v in (xc, yc, bw, bh))
            x1, y1 = int((xc - bw / 2) * w), int((yc - bh / 2) * h)
            x2, y2 = int((xc + bw / 2) * w), int((yc + bh / 2) * h)
            cv2.rectangle(img, (x1, y1), (x2, y2), colors[cls], 1)

        out_path = out_dir / f"{label_file.stem}_boxes.jpg"
        cv2.imwrite(str(out_path), img)
        print(f"wrote {out_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", default=str(config.RAW_DATA_DIR))
    parser.add_argument("--out", default=str(config.YOLO_DATASET_DIR))
    args = parser.parse_args()
    config.RAW_DATA_DIR = Path(args.src)
    dataset_dir = Path(args.out)

    video_ids = discover_videos()
    print(f"found {len(video_ids)} labeled videos: {video_ids}")

    split = load_or_create_split(video_ids)
    print(f"train videos ({len(split['train'])}): {split['train']}")
    print(f"test videos  ({len(split['test'])}): {split['test']}")

    n_train = populate_split(
        split["train"], "train", dataset_dir, stride=config.FRAME_SAMPLE_STRIDE
    )
    n_test = populate_split(
        split["test"], "test", dataset_dir, stride=config.FRAME_SAMPLE_STRIDE
    )
    print(f"copied {n_train} train frames, {n_test} test frames into {dataset_dir}")

    write_data_yaml(dataset_dir)

    sample_dir = config.REPO_ROOT / "samples" / "label_check"
    draw_sample_frames(split["train"], sample_dir)


if __name__ == "__main__":
    main()
