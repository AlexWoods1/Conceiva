"""Fine-tunes the YOLO detector on the VISEM-Tracking YOLO dataset."""

import argparse
import shutil

from ultralytics import YOLO

import config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default=str(config.YOLO_DATASET_DIR / "data.yaml"))
    parser.add_argument("--epochs", type=int, default=config.TRAIN_EPOCHS)
    parser.add_argument("--imgsz", type=int, default=config.TRAIN_IMGSZ)
    parser.add_argument("--device", default=config.TRAIN_DEVICE)
    args = parser.parse_args()

    model = YOLO(config.YOLO_BASE_MODEL)
    results = model.train(
        data=args.data,
        epochs=args.epochs,
        imgsz=args.imgsz,
        device=args.device,
        project=str(config.REPO_ROOT / "runs"),
        name="sperm_detector",
    )

    best = results.save_dir / "weights" / "best.pt"
    config.WEIGHTS_DIR.mkdir(exist_ok=True)
    shutil.copy(best, config.BEST_WEIGHTS_PATH)
    print(f"best weights copied to {config.BEST_WEIGHTS_PATH}")

    metrics = model.val(data=args.data, split="val", device=args.device)
    print(f"mAP50: {metrics.box.map50:.4f}  mAP50-95: {metrics.box.map:.4f}")


if __name__ == "__main__":
    main()
