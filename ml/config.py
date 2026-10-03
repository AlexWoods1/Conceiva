"""Every threshold and constant for the ml pipeline lives here."""

from pathlib import Path

ML_DIR = Path(__file__).resolve().parent
REPO_ROOT = ML_DIR.parent
RAW_DATA_DIR = (
    REPO_ROOT / "data" / "VISEM_Tracking" / "VISEM_Tracking_Train_v4" / "Train"
)
YOLO_DATASET_DIR = REPO_ROOT / "data" / "yolo"

# Class ids as shipped in the VISEM-Tracking labels.
CLASS_NAMES = {
    0: "sperm",
    1: "cluster",
    2: "small_or_pinhead",
}

# Split is by whole video, never by frame (frames within a video are
# near-duplicates, a frame split would leak and inflate scores).
# Fixed once and written to DATASET_SPLIT_FILE below so it never changes
# between runs. These 4 videos double as the demo videos.
SPLIT_SEED = 42
NUM_TEST_VIDEOS = 4
DATASET_SPLIT_FILE = ML_DIR / "dataset_split.json"

# Consecutive video frames are near-duplicates, so detection training keeps
# every Nth frame per video instead of all of them. Guess, tune if mAP suffers.
FRAME_SAMPLE_STRIDE = 5

# Detection training. Smallest YOLO model, since CPU inference must stay fast
# and the demo machine has no CUDA GPU (Apple M1, trains via MPS).
YOLO_BASE_MODEL = "yolo11n.pt"
TRAIN_EPOCHS = 15
TRAIN_IMGSZ = 640
TRAIN_DEVICE = "mps"
WEIGHTS_DIR = REPO_ROOT / "weights"
BEST_WEIGHTS_PATH = WEIGHTS_DIR / "sperm_detector.pt"

# Tracking. ByteTrack ships with ultralytics; these knobs are starting
# guesses, not tuned yet.
TRACK_CONF_THRESHOLD = 0.25
TRACKER_CONFIG = "bytetrack.yaml"
TRAIL_LENGTH = 30  # frames of motion trail drawn behind each tracked sperm

# Motility metrics (ml/motility.py).

# No magnification/sensor info ships with VISEM-Tracking, so this is the
# fallback estimate from the dataset docs: median long side of class-0
# (normal sperm) boxes across 2000 sampled label files was 23px, assuming a
# sperm head is ~5 microns long. This is an approximation, not a calibrated
# scale -- label it as such anywhere it reaches the UI.
MICRONS_PER_PIXEL = 5 / 23

# Ignore tracks shorter than this -- too little data to trust a velocity.
MIN_TRACK_SECONDS = 0.5

# Classification thresholds. Not official WHO cutoffs (WHO categorizes by
# visual grade a/b/c/d, not a published VSL number -- every CASA system
# calibrates its own). Tuned by grid search (ml/evaluate.py --split train)
# against lab ground truth on the 16 training videos, minimizing MAE against
# lab-reported progressive and total motility %. Not re-tuned since.
PROGRESSIVE_VSL_MIN = 8  # microns/sec
RAPID_VSL_MIN = 25  # microns/sec, unused until the rapid/slow breakdown is built
IMMOTILE_VCL_MAX = 5  # microns/sec, must sit above tracker jitter on a still sperm

# WHO laboratory manual, 6th edition (2021) lower reference limits (5th
# centile). These ARE the published values, used only for the UI comparison.
WHO_PROGRESSIVE_MOTILITY_MIN_PCT = 30
WHO_TOTAL_MOTILITY_MIN_PCT = 42
