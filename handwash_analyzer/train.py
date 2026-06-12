"""Train the WHO hand washing sequence classifier."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import List, Sequence, Tuple

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupShuffleSplit, train_test_split
from sklearn.preprocessing import LabelEncoder

from .utils import (
    LABEL_ENCODER_PATH,
    MODEL_PATH,
    NORMALIZER_PATH,
    PROCESSED_CSV_PATH,
    SEQUENCE_LENGTH_DEFAULT,
    WHO_STEPS,
    ensure_dirs,
    landmark_columns,
    normalize_dataframe_features,
    safe_import_tensorflow,
    save_json,
    save_pickle,
    setup_logging,
)

LOGGER = logging.getLogger(__name__)


def build_sequences(df: pd.DataFrame, feature_cols: Sequence[str], sequence_length: int, stride: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    sequences: List[np.ndarray] = []
    labels: List[str] = []
    groups: List[str] = []
    for video_id, group in df.sort_values(["video_id", "frame_idx"]).groupby("video_id"):
        values = group.loc[:, feature_cols].astype(np.float32).values
        label_values = group["label"].values
        if len(values) < sequence_length:
            pad_count = sequence_length - len(values)
            padded = np.vstack([np.zeros((pad_count, len(feature_cols)), dtype=np.float32), values])
            sequences.append(padded)
            labels.append(str(label_values[-1]))
            groups.append(str(video_id))
            continue
        for start in range(0, len(values) - sequence_length + 1, stride):
            end = start + sequence_length
            sequences.append(values[start:end])
            labels.append(str(label_values[end - 1]))
            groups.append(str(video_id))
    if not sequences:
        raise RuntimeError("No training sequences could be generated from the processed CSV.")
    return np.asarray(sequences, dtype=np.float32), np.asarray(labels), np.asarray(groups)


def make_model(input_shape: Tuple[int, int], num_classes: int):
    tf = safe_import_tensorflow()
    keras = tf.keras
    model = keras.Sequential(
        [
            keras.layers.Input(shape=input_shape),
            keras.layers.Masking(mask_value=0.0),
            keras.layers.Bidirectional(keras.layers.LSTM(96, return_sequences=True)),
            keras.layers.Dropout(0.30),
            keras.layers.Bidirectional(keras.layers.LSTM(64)),
            keras.layers.Dense(96, activation="relu"),
            keras.layers.Dropout(0.30),
            keras.layers.Dense(num_classes, activation="softmax"),
        ]
    )
    model.compile(
        optimizer=keras.optimizers.Adam(learning_rate=1e-3),
        loss="sparse_categorical_crossentropy",
        metrics=["accuracy"],
    )
    return model


class Trainer:
    def __init__(self, csv_path: Path, sequence_length: int, stride: int, epochs: int, batch_size: int):
        self.csv_path = Path(csv_path)
        self.sequence_length = sequence_length
        self.stride = stride
        self.epochs = epochs
        self.batch_size = batch_size

    def train(self) -> Path:
        tf = safe_import_tensorflow()
        ensure_dirs()
        if not self.csv_path.exists():
            raise FileNotFoundError(f"Processed CSV not found: {self.csv_path}. Run python dataset_builder.py first.")

        df = pd.read_csv(self.csv_path)
        required = {"video_id", "frame_idx", "label"}
        missing = required.difference(df.columns)
        if missing:
            raise ValueError(f"Processed CSV is missing required columns: {sorted(missing)}")

        feature_cols = [c for c in landmark_columns() if c in df.columns]
        if not feature_cols:
            raise ValueError("No landmark feature columns were found in the processed CSV.")

        df = df[df["label"].isin(WHO_STEPS)].copy()
        if df.empty:
            raise ValueError("No rows match the supported WHO step labels.")

        df, normalizer = normalize_dataframe_features(df, feature_cols)
        save_json(normalizer, NORMALIZER_PATH)

        x, labels, groups = build_sequences(df, feature_cols, self.sequence_length, self.stride)
        encoder = LabelEncoder()
        encoder.fit(WHO_STEPS)
        y = encoder.transform(labels)
        save_pickle(encoder, LABEL_ENCODER_PATH)

        if len(np.unique(groups)) > 1:
            splitter = GroupShuffleSplit(test_size=0.2, n_splits=1, random_state=42)
            train_idx, val_idx = next(splitter.split(x, y, groups))
        else:
            train_idx, val_idx = train_test_split(np.arange(len(x)), test_size=0.2, random_state=42, stratify=y if len(np.unique(y)) > 1 else None)

        model = make_model((self.sequence_length, x.shape[-1]), len(encoder.classes_))
        callbacks = [
            tf.keras.callbacks.EarlyStopping(monitor="val_loss", patience=8, restore_best_weights=True),
            tf.keras.callbacks.ModelCheckpoint(str(MODEL_PATH), monitor="val_loss", save_best_only=True),
            tf.keras.callbacks.ReduceLROnPlateau(monitor="val_loss", factor=0.5, patience=3),
        ]
        LOGGER.info("Training on %d sequences, validating on %d sequences", len(train_idx), len(val_idx))
        model.fit(
            x[train_idx],
            y[train_idx],
            validation_data=(x[val_idx], y[val_idx]),
            epochs=self.epochs,
            batch_size=self.batch_size,
            callbacks=callbacks,
            verbose=1,
        )
        if not MODEL_PATH.exists():
            model.save(str(MODEL_PATH))
        LOGGER.info("Saved model to %s", MODEL_PATH)
        return MODEL_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train the WHO hand wash sequence classifier.")
    parser.add_argument("--csv", type=Path, default=PROCESSED_CSV_PATH, help="Processed landmarks CSV.")
    parser.add_argument("--sequence-length", type=int, default=SEQUENCE_LENGTH_DEFAULT, help="Temporal window length in frames.")
    parser.add_argument("--stride", type=int, default=5, help="Sliding window stride for training sequences.")
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=32)
    return parser.parse_args()


def main() -> None:
    setup_logging()
    args = parse_args()
    Trainer(args.csv, args.sequence_length, args.stride, args.epochs, args.batch_size).train()


if __name__ == "__main__":
    main()
