"""Shared utilities for WHO Hand Wash Analyzer."""

from __future__ import annotations

import json
import logging
import math
import os
import pickle
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import cv2
import numpy as np
import pandas as pd


ROOT_DIR = Path(__file__).resolve().parent.parent
DATASETS_DIR = ROOT_DIR / "datasets"
MODELS_DIR = ROOT_DIR / "models"
OUTPUT_DIR = ROOT_DIR / "output"

MODEL_PATH = MODELS_DIR / "handwash_model.h5"
HAND_LANDMARKER_PATH = MODELS_DIR / "hand_landmarker.task"
HAND_LANDMARKER_URL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
LABEL_ENCODER_PATH = MODELS_DIR / "label_encoder.pkl"
NORMALIZER_PATH = MODELS_DIR / "normalization.json"
PROCESSED_CSV_PATH = DATASETS_DIR / "processed_landmarks.csv"

SEQUENCE_LENGTH_DEFAULT = 45
NUM_CLASSES = 12
HAND_LANDMARK_COUNT = 21
FEATURE_HANDS = ("Left", "Right")

WHO_STEPS = [
    "Step 1",
    "Step 2 Left",
    "Step 2 Right",
    "Step 3",
    "Step 4 Left",
    "Step 4 Right",
    "Step 5 Left",
    "Step 5 Right",
    "Step 6 Left",
    "Step 6 Right",
    "Step 7 Left",
    "Step 7 Right",
]


@dataclass
class StepResult:
    label: str
    completed: bool
    confidence: float
    first_frame: Optional[int] = None
    last_frame: Optional[int] = None


@dataclass
class AnalysisReport:
    video_path: str
    total_frames: int
    fps: float
    completed_steps: List[str]
    missing_steps: List[str]
    compliance_percentage: float
    per_step: Dict[str, StepResult]
    predictions_csv: Optional[str] = None
    annotated_video: Optional[str] = None

    def to_json_dict(self) -> dict:
        data = asdict(self)
        data["per_step"] = {k: asdict(v) for k, v in self.per_step.items()}
        return data


def ensure_dirs() -> None:
    for directory in (DATASETS_DIR, MODELS_DIR, OUTPUT_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def setup_logging(log_file: Optional[Path] = None, level: int = logging.INFO) -> None:
    ensure_dirs()
    handlers: List[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def infer_label_from_path(path: Path) -> Optional[str]:
    """Infer one of the known WHO labels from a Kaggle clip path."""
    clean_parts = [p.replace("_", " ").replace("-", " ").strip().lower() for p in path.parts]
    joined = " ".join(clean_parts)
    for label in sorted(WHO_STEPS, key=len, reverse=True):
        normalized = label.lower()
        compact = normalized.replace(" ", "")
        if normalized in joined or compact in joined.replace(" ", ""):
            return label
    return None


def landmark_columns() -> List[str]:
    cols: List[str] = []
    for hand in FEATURE_HANDS:
        for idx in range(HAND_LANDMARK_COUNT):
            cols.extend([f"{hand}_{idx}_x", f"{hand}_{idx}_y", f"{hand}_{idx}_z"])
    cols.extend(["inter_wrist_distance", "left_visible", "right_visible"])
    cols.extend(angle_columns())
    return cols


def angle_columns() -> List[str]:
    cols: List[str] = []
    triples = finger_angle_triples()
    for hand in FEATURE_HANDS:
        for name in triples:
            cols.append(f"{hand}_{name}_angle")
    return cols


def finger_angle_triples() -> Dict[str, Tuple[int, int, int]]:
    return {
        "thumb_mcp": (1, 2, 3),
        "thumb_ip": (2, 3, 4),
        "index_pip": (5, 6, 7),
        "index_dip": (6, 7, 8),
        "middle_pip": (9, 10, 11),
        "middle_dip": (10, 11, 12),
        "ring_pip": (13, 14, 15),
        "ring_dip": (14, 15, 16),
        "pinky_pip": (17, 18, 19),
        "pinky_dip": (18, 19, 20),
    }


def normalize_single_hand(points: np.ndarray) -> np.ndarray:
    """Make landmarks translation, scale, and approximately rotation invariant."""
    if points.shape != (HAND_LANDMARK_COUNT, 3) or np.allclose(points, 0):
        return np.zeros((HAND_LANDMARK_COUNT, 3), dtype=np.float32)

    normalized = points.astype(np.float32).copy()
    wrist = normalized[0].copy()
    normalized -= wrist

    scale = np.linalg.norm(normalized[9, :2])
    if scale < 1e-6:
        scale = np.max(np.linalg.norm(normalized[:, :2], axis=1))
    if scale < 1e-6:
        return np.zeros((HAND_LANDMARK_COUNT, 3), dtype=np.float32)
    normalized /= scale

    middle = normalized[9, :2]
    angle = math.atan2(float(middle[1]), float(middle[0]))
    target = -math.pi / 2.0
    theta = target - angle
    rot = np.array(
        [[math.cos(theta), -math.sin(theta)], [math.sin(theta), math.cos(theta)]],
        dtype=np.float32,
    )
    normalized[:, :2] = normalized[:, :2] @ rot.T
    return normalized


def angle_between(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
    ba = a - b
    bc = c - b
    denom = np.linalg.norm(ba) * np.linalg.norm(bc)
    if denom < 1e-6:
        return 0.0
    cosine = float(np.clip(np.dot(ba, bc) / denom, -1.0, 1.0))
    return math.acos(cosine) / math.pi


def landmarks_to_feature_vector(hand_points: Dict[str, Optional[np.ndarray]]) -> np.ndarray:
    """Convert raw MediaPipe hand landmarks into a stable feature vector."""
    left_raw = hand_points.get("Left")
    right_raw = hand_points.get("Right")
    left = normalize_single_hand(left_raw) if left_raw is not None else np.zeros((21, 3), dtype=np.float32)
    right = normalize_single_hand(right_raw) if right_raw is not None else np.zeros((21, 3), dtype=np.float32)

    features: List[float] = []
    features.extend(left.reshape(-1).tolist())
    features.extend(right.reshape(-1).tolist())

    if left_raw is not None and right_raw is not None:
        inter_wrist = float(np.linalg.norm(left_raw[0, :2] - right_raw[0, :2]))
    else:
        inter_wrist = 0.0
    features.extend([inter_wrist, 1.0 if left_raw is not None else 0.0, 1.0 if right_raw is not None else 0.0])

    triples = finger_angle_triples()
    for points in (left, right):
        for triple in triples.values():
            features.append(angle_between(points[triple[0]], points[triple[1]], points[triple[2]]))
    return np.asarray(features, dtype=np.float32)


def extract_hands_from_mediapipe(results) -> Dict[str, Optional[np.ndarray]]:
    hands: Dict[str, Optional[np.ndarray]] = {"Left": None, "Right": None}
    if hasattr(results, "hand_landmarks"):
        handedness = getattr(results, "handedness", []) or []
        for idx, hand_landmarks in enumerate(results.hand_landmarks or []):
            label = "Left"
            if idx < len(handedness) and handedness[idx]:
                label = getattr(handedness[idx][0], "category_name", "Left")
            points = np.array([[lm.x, lm.y, lm.z] for lm in hand_landmarks], dtype=np.float32)
            hands[label] = points
        return hands
    if not results or not results.multi_hand_landmarks:
        return hands
    handedness = results.multi_handedness or []
    for idx, hand_landmarks in enumerate(results.multi_hand_landmarks):
        label = "Left"
        if idx < len(handedness):
            label = handedness[idx].classification[0].label
        points = np.array([[lm.x, lm.y, lm.z] for lm in hand_landmarks.landmark], dtype=np.float32)
        hands[label] = points
    return hands


def ensure_hand_landmarker_model(path: Path = HAND_LANDMARKER_PATH) -> Path:
    ensure_dirs()
    if path.exists() and path.stat().st_size > 0:
        return path
    logging.getLogger(__name__).info("Downloading MediaPipe hand landmarker model to %s", path)
    urllib.request.urlretrieve(HAND_LANDMARKER_URL, path)
    return path


class MediaPipeHandsCompat:
    """Compatibility wrapper for old MediaPipe Solutions and new Tasks API."""

    def __init__(self, max_num_hands: int = 2, min_detection_confidence: float = 0.45, min_tracking_confidence: float = 0.45):
        import mediapipe as mp

        self.mode = "tasks"
        self.mp = mp
        self.hands = None
        self.drawer = None
        if hasattr(mp, "solutions"):
            self.mode = "solutions"
            self.hands = mp.solutions.hands.Hands(
                static_image_mode=False,
                max_num_hands=max_num_hands,
                min_detection_confidence=min_detection_confidence,
                min_tracking_confidence=min_tracking_confidence,
            )
            self.drawer = mp.solutions.drawing_utils
            self.connections = mp.solutions.hands.HAND_CONNECTIONS
        else:
            from mediapipe.tasks import python
            from mediapipe.tasks.python import vision

            model_path = ensure_hand_landmarker_model()
            base_options = python.BaseOptions(model_asset_path=str(model_path))
            options = vision.HandLandmarkerOptions(
                base_options=base_options,
                num_hands=max_num_hands,
                min_hand_detection_confidence=min_detection_confidence,
                min_hand_presence_confidence=min_tracking_confidence,
                min_tracking_confidence=min_tracking_confidence,
            )
            self.hands = vision.HandLandmarker.create_from_options(options)

    def process(self, rgb_frame: np.ndarray):
        if self.mode == "solutions":
            return self.hands.process(rgb_frame)
        image = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb_frame)
        return self.hands.detect(image)

    def draw(self, frame: np.ndarray, results) -> None:
        if self.mode == "solutions":
            if results.multi_hand_landmarks:
                for hand_landmarks in results.multi_hand_landmarks:
                    self.drawer.draw_landmarks(frame, hand_landmarks, self.connections)
            return
        for hand_landmarks in getattr(results, "hand_landmarks", []) or []:
            h, w = frame.shape[:2]
            points = [(int(lm.x * w), int(lm.y * h)) for lm in hand_landmarks]
            for start, end in HAND_CONNECTIONS:
                if start < len(points) and end < len(points):
                    cv2.line(frame, points[start], points[end], (85, 220, 120), 2)
            for point in points:
                cv2.circle(frame, point, 3, (255, 255, 255), -1)

    def close(self) -> None:
        if self.hands:
            self.hands.close()


HAND_CONNECTIONS = (
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (0, 17), (17, 18), (18, 19), (19, 20),
)


def draw_overlay(
    frame: np.ndarray,
    current_label: str,
    confidence: float,
    completed_steps: Iterable[str],
    compliance: float,
) -> np.ndarray:
    completed = set(completed_steps)
    overlay = frame.copy()
    cv2.rectangle(overlay, (8, 8), (470, 138), (0, 0, 0), -1)
    frame = cv2.addWeighted(overlay, 0.55, frame, 0.45, 0)
    cv2.putText(frame, f"Detected: {current_label} ({confidence:.2f})", (20, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (255, 255, 255), 2)
    cv2.putText(frame, f"Compliance: {compliance:.1f}%", (20, 68), cv2.FONT_HERSHEY_SIMPLEX, 0.68, (75, 220, 120), 2)
    step_line = "Completed: " + ", ".join([s.replace("Step ", "S") for s in WHO_STEPS if s in completed][:6])
    cv2.putText(frame, step_line[:68], (20, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (220, 235, 255), 1)
    cv2.putText(frame, f"{len(completed)}/{len(WHO_STEPS)} WHO actions", (20, 126), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (220, 235, 255), 1)
    return frame


def save_pickle(obj: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        pickle.dump(obj, f)


def load_pickle(path: Path) -> object:
    with path.open("rb") as f:
        return pickle.load(f)


def save_json(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def normalize_dataframe_features(df: pd.DataFrame, feature_cols: Sequence[str]) -> Tuple[pd.DataFrame, Dict[str, List[float]]]:
    values = df.loc[:, feature_cols].astype(np.float32).values
    mean = values.mean(axis=0)
    std = values.std(axis=0)
    std[std < 1e-6] = 1.0
    df = df.copy()
    df.loc[:, feature_cols] = (values - mean) / std
    return df, {"feature_columns": list(feature_cols), "mean": mean.tolist(), "std": std.tolist()}


def apply_normalization(values: np.ndarray, normalizer: Optional[dict]) -> np.ndarray:
    if not normalizer:
        return values.astype(np.float32)
    mean = np.asarray(normalizer["mean"], dtype=np.float32)
    std = np.asarray(normalizer["std"], dtype=np.float32)
    std[std < 1e-6] = 1.0
    return ((values.astype(np.float32) - mean) / std).astype(np.float32)


def write_report_files(report: AnalysisReport, predictions: pd.DataFrame, output_dir: Path = OUTPUT_DIR) -> Tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(report.video_path).stem
    json_path = output_dir / f"{stem}_report.json"
    csv_path = output_dir / f"{stem}_predictions.csv"
    save_json(report.to_json_dict(), json_path)
    predictions.to_csv(csv_path, index=False)
    return json_path, csv_path


def validate_video_path(path: str) -> Path:
    video = Path(path)
    if not video.exists():
        raise FileNotFoundError(f"Video not found: {video}")
    if video.suffix.lower() != ".mp4":
        raise ValueError("Please select an .mp4 file.")
    return video


def safe_import_tensorflow():
    try:
        import tensorflow as tf

        return tf
    except Exception as exc:
        raise RuntimeError(
            "TensorFlow/Keras is required for training or model inference. "
            "Install dependencies from requirements.txt. On Python 3.13, use tensorflow>=2.20 or a supported Python build. "
            f"Python executable: {os.sys.executable}. Original error: {type(exc).__name__}: {exc}"
        ) from exc
