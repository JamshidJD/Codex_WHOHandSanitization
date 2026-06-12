"""Build landmark CSV features from the Kaggle hand-wash video dataset."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Dict, Iterable, List

import cv2
import pandas as pd
from tqdm import tqdm

from .utils import (
    PROCESSED_CSV_PATH,
    ensure_dirs,
    extract_hands_from_mediapipe,
    infer_label_from_path,
    landmark_columns,
    landmarks_to_feature_vector,
    MediaPipeHandsCompat,
    setup_logging,
)

LOGGER = logging.getLogger(__name__)


class DatasetBuilder:
    def __init__(
        self,
        dataset_dir: Path,
        output_csv: Path = PROCESSED_CSV_PATH,
        frame_stride: int = 1,
        max_frames_per_video: int = 0,
        target_fps: float = 5.0,
    ):
        self.dataset_dir = Path(dataset_dir)
        self.output_csv = Path(output_csv)
        self.frame_stride = max(1, frame_stride)
        self.max_frames_per_video = max_frames_per_video
        self.target_fps = max(0.0, target_fps)

    def find_videos(self) -> List[Path]:
        if not self.dataset_dir.exists():
            raise FileNotFoundError(
                f"Dataset folder does not exist: {self.dataset_dir}\n"
                "Download and unzip the Kaggle dataset first:\n"
                "  python download_dataset.py\n"
                "or manually extract it into datasets/raw"
            )
        videos = sorted(self.dataset_dir.rglob("*.mp4"))
        if not videos:
            found = sorted(p.suffix.lower() for p in self.dataset_dir.rglob("*") if p.is_file())
            suffixes = ", ".join(sorted(set(found))[:12]) if found else "no files"
            raise FileNotFoundError(
                f"No .mp4 files found under {self.dataset_dir}. Found: {suffixes}\n"
                "Download and unzip the Kaggle dataset first:\n"
                "  python download_dataset.py\n"
                "or manually extract the dataset so videos live somewhere under datasets/raw"
            )
        return videos

    def build(self) -> Path:
        ensure_dirs()
        videos = self.find_videos()
        feature_cols = landmark_columns()
        existing_videos = self._existing_video_ids(videos)
        if existing_videos:
            LOGGER.info("Resuming build; %d videos already present in %s", len(existing_videos), self.output_csv)

        hands = MediaPipeHandsCompat(
            max_num_hands=2,
            min_detection_confidence=0.45,
            min_tracking_confidence=0.45,
        )

        try:
            for video_path in tqdm(videos, desc="Processing videos"):
                if video_path.stem in existing_videos:
                    continue
                label = infer_label_from_path(video_path)
                if label is None:
                    LOGGER.warning("Skipping %s because no WHO label could be inferred from its path", video_path)
                    continue
                rows = self._process_video(video_path, label, hands, feature_cols)
                self._append_rows(rows)
        finally:
            hands.close()

        if not self.output_csv.exists():
            raise RuntimeError("No rows were generated. Check dataset layout and class folder names.")
        df = pd.read_csv(self.output_csv, usecols=["video_id"])
        LOGGER.info("Wrote %d landmark rows to %s", len(df), self.output_csv)
        return self.output_csv

    def _effective_stride(self, fps: float) -> int:
        if self.frame_stride > 1:
            return self.frame_stride
        if self.target_fps <= 0 or fps <= 0:
            return 1
        return max(1, round(fps / self.target_fps))

    def _existing_video_ids(self, videos: List[Path]) -> set[str]:
        if not self.output_csv.exists() or self.output_csv.stat().st_size == 0:
            return set()
        try:
            df = pd.read_csv(self.output_csv, usecols=["video_id", "frame_idx", "source_total_frames", "effective_stride"])
            complete: set[str] = set()
            if self.max_frames_per_video:
                return set(df["video_id"].astype(str).unique())
            grouped = df.groupby("video_id").agg({"frame_idx": "max", "source_total_frames": "max", "effective_stride": "max"})
            for video in videos:
                if video.stem not in grouped.index:
                    continue
                row = grouped.loc[video.stem]
                total = int(row["source_total_frames"] or 0)
                stride = max(1, int(row["effective_stride"] or self.frame_stride))
                max_seen = int(row["frame_idx"])
                if total and max_seen >= total - stride:
                    complete.add(video.stem)
            all_seen = set(df["video_id"].astype(str).unique())
            incomplete = all_seen.difference(complete)
            if incomplete:
                LOGGER.warning("Removing %d incomplete video entries from %s before resuming", len(incomplete), self.output_csv)
                df = df[df["video_id"].astype(str).isin(complete)]
                if df.empty:
                    self.output_csv.unlink(missing_ok=True)
                else:
                    df.to_csv(self.output_csv, index=False)
            return complete
        except Exception:
            LOGGER.warning("Existing CSV could not be read and will be overwritten: %s", self.output_csv)
            self.output_csv.unlink(missing_ok=True)
            return set()

    def _append_rows(self, rows: List[Dict[str, object]]) -> None:
        if not rows:
            return
        self.output_csv.parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame(rows)
        df.to_csv(self.output_csv, mode="a", header=not self.output_csv.exists(), index=False)

    def _process_video(self, video_path: Path, label: str, hands, feature_cols: Iterable[str]) -> List[Dict[str, object]]:
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            LOGGER.warning("Could not open %s", video_path)
            return []

        rows: List[Dict[str, object]] = []
        frame_idx = 0
        kept = 0
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        effective_stride = self._effective_stride(fps)
        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                if frame_idx % effective_stride != 0:
                    frame_idx += 1
                    continue
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                results = hands.process(rgb)
                hand_points = extract_hands_from_mediapipe(results)
                features = landmarks_to_feature_vector(hand_points)
                row: Dict[str, object] = {
                    "video_path": str(video_path),
                    "video_id": video_path.stem,
                    "frame_idx": frame_idx,
                    "sample_idx": kept,
                    "source_fps": fps,
                    "source_total_frames": total_frames,
                    "effective_stride": effective_stride,
                    "sampled_fps": fps / effective_stride if fps else 0.0,
                    "label": label,
                }
                row.update({col: float(value) for col, value in zip(feature_cols, features)})
                rows.append(row)
                kept += 1
                frame_idx += 1
                if self.max_frames_per_video and kept >= self.max_frames_per_video:
                    break
        finally:
            cap.release()
        return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract MediaPipe hand landmarks from Kaggle hand-wash videos.")
    parser.add_argument("--dataset-dir", type=Path, default=Path("datasets/raw"), help="Folder containing downloaded Kaggle dataset videos.")
    parser.add_argument("--output", type=Path, default=PROCESSED_CSV_PATH, help="Output CSV path.")
    parser.add_argument("--frame-stride", type=int, default=1, help="Process every Nth frame. Overrides --target-fps when greater than 1.")
    parser.add_argument("--target-fps", type=float, default=5.0, help="Automatically skip frames to sample each source video near this FPS. Use 0 to process all frames.")
    parser.add_argument("--max-frames-per-video", type=int, default=0, help="Optional cap per video; 0 means all frames.")
    return parser.parse_args()


def main() -> None:
    setup_logging()
    args = parse_args()
    builder = DatasetBuilder(args.dataset_dir, args.output, args.frame_stride, args.max_frames_per_video, args.target_fps)
    builder.build()


if __name__ == "__main__":
    main()
