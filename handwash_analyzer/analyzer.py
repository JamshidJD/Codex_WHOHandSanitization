"""Video inference engine for WHO Hand Wash Analyzer."""

from __future__ import annotations

import logging
from collections import Counter, deque
from pathlib import Path
from typing import Callable, Deque, Dict, List, Optional, Tuple

import cv2
import numpy as np
import pandas as pd

from .utils import (
    AnalysisReport,
    LABEL_ENCODER_PATH,
    MODEL_PATH,
    NORMALIZER_PATH,
    OUTPUT_DIR,
    SEQUENCE_LENGTH_DEFAULT,
    StepResult,
    MediaPipeHandsCompat,
    WHO_STEPS,
    apply_normalization,
    draw_overlay,
    ensure_dirs,
    extract_hands_from_mediapipe,
    landmark_columns,
    landmarks_to_feature_vector,
    load_json,
    load_pickle,
    safe_import_tensorflow,
    save_json,
    validate_video_path,
)

LOGGER = logging.getLogger(__name__)


ProgressCallback = Callable[[int, np.ndarray, str, float], None]


class ModelUnavailable(RuntimeError):
    pass


class HandWashAnalyzer:
    def __init__(
        self,
        model_path: Path = MODEL_PATH,
        confidence_threshold: float = 0.8,
        min_duration_frames: int = 15,
        sequence_length: int = SEQUENCE_LENGTH_DEFAULT,
        smoothing_window: int = 12,
    ):
        self.model_path = Path(model_path)
        self.confidence_threshold = confidence_threshold
        self.min_duration_frames = min_duration_frames
        self.sequence_length = sequence_length
        self.smoothing_window = smoothing_window
        self.stop_requested = False
        self.model = None
        self.encoder = None
        self.normalizer = None
        self.feature_cols = landmark_columns()

    def request_stop(self) -> None:
        self.stop_requested = True

    def load_model(self) -> None:
        if not self.model_path.exists():
            raise ModelUnavailable(
                f"Trained model not found at {self.model_path}. Run python dataset_builder.py and python train.py first."
            )
        tf = safe_import_tensorflow()
        self.model = tf.keras.models.load_model(str(self.model_path))
        if LABEL_ENCODER_PATH.exists():
            self.encoder = load_pickle(LABEL_ENCODER_PATH)
        if NORMALIZER_PATH.exists():
            self.normalizer = load_json(NORMALIZER_PATH)
            self.feature_cols = self.normalizer.get("feature_columns", self.feature_cols)

    def analyze(
        self,
        video_path: str,
        show_live: bool = False,
        export_video: bool = True,
        frame_skip: int = 1,
        processing_scale: float = 1.0,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> Tuple[AnalysisReport, pd.DataFrame]:
        ensure_dirs()
        self.stop_requested = False
        frame_skip = max(1, int(frame_skip))
        processing_scale = float(np.clip(processing_scale, 0.25, 1.0))
        self.load_model()
        video = validate_video_path(video_path)
        cap = cv2.VideoCapture(str(video))
        if not cap.isOpened():
            raise RuntimeError(f"Unable to open video: {video}")

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 640)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 480)

        writer = None
        annotated_path: Optional[Path] = None
        if export_video:
            annotated_path = OUTPUT_DIR / f"{video.stem}_annotated.mp4"
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer_fps = fps / frame_skip if fps and frame_skip > 1 else fps
            writer = cv2.VideoWriter(str(annotated_path), fourcc, writer_fps, (width, height))

        per_step = {label: StepResult(label=label, completed=False, confidence=0.0) for label in WHO_STEPS}
        sequence: Deque[np.ndarray] = deque(maxlen=self.sequence_length)
        smoothed: Deque[Tuple[str, float]] = deque(maxlen=self.smoothing_window)
        held_counts: Counter[str] = Counter()
        prediction_rows: List[Dict[str, object]] = []

        hands = MediaPipeHandsCompat(max_num_hands=2, min_detection_confidence=0.45, min_tracking_confidence=0.45)
        current_label = "Waiting for hands"
        current_conf = 0.0

        try:
            frame_idx = 0
            while True:
                if self.stop_requested:
                    break
                ok, frame = cap.read()
                if not ok:
                    break
                if frame_idx % frame_skip != 0:
                    frame_idx += 1
                    continue
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                if processing_scale < 1.0:
                    process_rgb = cv2.resize(
                        rgb,
                        None,
                        fx=processing_scale,
                        fy=processing_scale,
                        interpolation=cv2.INTER_AREA,
                    )
                else:
                    process_rgb = rgb
                results = hands.process(process_rgb)
                hand_points = extract_hands_from_mediapipe(results)
                features = landmarks_to_feature_vector(hand_points)
                features = apply_normalization(features, self.normalizer)
                sequence.append(features)

                hands.draw(frame, results)

                if len(sequence) == self.sequence_length:
                    probs = self.model.predict(np.expand_dims(np.asarray(sequence, dtype=np.float32), axis=0), verbose=0)[0]
                    label_idx = int(np.argmax(probs))
                    current_conf = float(probs[label_idx])
                    if self.encoder is not None:
                        current_label = str(self.encoder.inverse_transform([label_idx])[0])
                    else:
                        current_label = WHO_STEPS[label_idx] if label_idx < len(WHO_STEPS) else f"class_{label_idx}"
                    smoothed.append((current_label, current_conf))
                    current_label, current_conf = self._smooth_prediction(smoothed)
                    self._update_completion(current_label, current_conf, frame_idx, held_counts, per_step)

                completed = [label for label, result in per_step.items() if result.completed]
                compliance = len(completed) / len(WHO_STEPS) * 100.0
                frame = draw_overlay(frame, current_label, current_conf, completed, compliance)

                prediction_rows.append(
                    {
                        "frame_idx": frame_idx,
                        "time_sec": frame_idx / fps if fps else 0.0,
                        "frame_skip": frame_skip,
                        "processing_scale": processing_scale,
                        "predicted_step": current_label,
                        "confidence": current_conf,
                        "completed_count": len(completed),
                        "compliance_percentage": compliance,
                    }
                )

                if writer:
                    writer.write(frame)
                if progress_callback:
                    percent = int((frame_idx + 1) * 100 / total_frames) if total_frames else 0
                    progress_callback(min(percent, 100), frame, current_label, current_conf)
                if show_live:
                    cv2.imshow("WHO Hand Wash Analyzer", frame)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
                frame_idx += 1
        finally:
            hands.close()
            cap.release()
            if writer:
                writer.release()
            if show_live:
                cv2.destroyAllWindows()

        completed = [label for label, result in per_step.items() if result.completed]
        missing = [label for label in WHO_STEPS if label not in completed]
        report = AnalysisReport(
            video_path=str(video),
            total_frames=total_frames,
            fps=fps,
            completed_steps=completed,
            missing_steps=missing,
            compliance_percentage=len(completed) / len(WHO_STEPS) * 100.0,
            per_step=per_step,
            annotated_video=str(annotated_path) if annotated_path else None,
        )
        predictions = pd.DataFrame(prediction_rows)
        csv_path = OUTPUT_DIR / f"{video.stem}_predictions.csv"
        predictions.to_csv(csv_path, index=False)
        report.predictions_csv = str(csv_path)
        json_path = OUTPUT_DIR / f"{video.stem}_report.json"
        save_json(report.to_json_dict(), json_path)
        LOGGER.info("Analysis complete. Report: %s CSV: %s", json_path, csv_path)
        return report, predictions

    def _smooth_prediction(self, smoothed: Deque[Tuple[str, float]]) -> Tuple[str, float]:
        if not smoothed:
            return "Waiting for hands", 0.0
        labels = [label for label, _ in smoothed]
        label = Counter(labels).most_common(1)[0][0]
        confidences = [conf for l, conf in smoothed if l == label]
        return label, float(np.mean(confidences)) if confidences else 0.0

    def _update_completion(
        self,
        label: str,
        confidence: float,
        frame_idx: int,
        held_counts: Counter[str],
        per_step: Dict[str, StepResult],
    ) -> None:
        if label not in per_step:
            return
        for key in list(held_counts.keys()):
            if key != label:
                held_counts[key] = 0
        if confidence >= self.confidence_threshold:
            held_counts[label] += 1
            result = per_step[label]
            result.confidence = max(result.confidence, confidence)
            if result.first_frame is None:
                result.first_frame = frame_idx
            result.last_frame = frame_idx
            if not result.completed and held_counts[label] >= self.min_duration_frames:
                result.completed = True
        else:
            held_counts[label] = 0
