# WHO Hand Wash Analyzer

Python desktop application for analyzing prerecorded MP4 videos of hand washing or sanitization against the 12 fine-grained WHO hand hygiene action classes.

## Features

- Load `.mp4` videos from the desktop GUI.
- Extract MediaPipe Hands landmarks frame by frame.
- Normalize landmarks for translation, scale, and wrist orientation.
- Train a BiLSTM sequence classifier on the Kaggle hand-wash dataset.
- Analyze videos with temporal smoothing, confidence thresholding, and duplicate suppression.
- Optional real-time playback with MediaPipe overlays.
- Export JSON report, CSV prediction timeline, and annotated MP4 into `output/`.

## Project Layout

```text
WHOHandSantization/
|-- app.py
|-- analyzer.py
|-- dataset_builder.py
|-- datasets/
|-- esp32cam/
|-- handwash_analyzer/
|-- models/
|-- received_videos/
|-- SmartSanitizerWeb/
|-- output/
`-- videoserver_tcp.py
```

Root-level wrappers are also provided:

```bash
python download_dataset.py
python dataset_builder.py
python train.py
python app.py
```

`SmartSanitizerWeb/` contains the ASP.NET Core SmartSanitizer API that was previously kept at `C:\VSO\SmartSanitizer`. It now lives inside this repository at:

```text
C:\VSO\WHOHandSantization\SmartSanitizerWeb
```

## Installation

Python 3.13 is installed in this workspace. TensorFlow support can vary by platform and Python version, so if `pip install tensorflow` fails on your machine, create a Python 3.11 or 3.12 virtual environment.

```bash
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Download Dataset

Primary dataset:

[Kaggle Hand Wash Dataset](https://www.kaggle.com/datasets/realtimear/hand-wash-dataset/data)

Option 1: use the Kaggle website, download the archive, and extract it under:

```text
datasets/raw/
```

Option 2: use the Kaggle CLI after authenticating:

```bash
kaggle auth login
```

You can also use a Kaggle API token at `%USERPROFILE%\.kaggle\kaggle.json`.

Then download:

```bash
python download_dataset.py
```

This is equivalent to:

```bash
kaggle datasets download -d realtimear/hand-wash-dataset -p datasets/raw --unzip
```

Check that videos exist before preprocessing:

```bash
dir /s datasets\raw\*.mp4
```

The builder infers labels from folder and file names matching:

- Step 1
- Step 2 Left
- Step 2 Right
- Step 3
- Step 4 Left
- Step 4 Right
- Step 5 Left
- Step 5 Right
- Step 6 Left
- Step 6 Right
- Step 7 Left
- Step 7 Right

## Build Landmark Dataset

```bash
python dataset_builder.py --dataset-dir datasets/raw
```

By default, preprocessing uses frame skipping to sample each video at about 5 FPS. This is much faster than processing every source frame and is usually enough for temporal hand-wash action recognition.

Output:

```text
datasets/processed_landmarks.csv
```

Useful options:

```bash
python dataset_builder.py --dataset-dir datasets/raw --target-fps 8
python dataset_builder.py --dataset-dir datasets/raw --target-fps 0
python dataset_builder.py --dataset-dir datasets/raw --frame-stride 2
python dataset_builder.py --dataset-dir datasets/raw --max-frames-per-video 300
```

Use `--target-fps 0` only when you really want all frames. Use `--frame-stride N` to process exactly every Nth frame; it overrides target-FPS sampling.

## Train Model

```bash
python train.py
```

Outputs:

```text
models/handwash_model.h5
models/label_encoder.pkl
models/normalization.json
```

The model uses:

- MediaPipe landmarks for both hands
- one-hand visibility indicators
- inter-wrist distance
- relative joint angles
- 45-frame temporal windows by default
- BiLSTM + Dense + Dropout + Softmax

## Run Desktop App

```bash
python app.py
```

Use the GUI to:

1. Browse for an MP4.
2. Start analysis.
3. Optionally enable live processing.
4. Review completed and missing WHO steps.
5. Open exported files from `output/`.

## CLI Analysis

```bash
python analyzer.py path\to\video.mp4
python analyzer.py path\to\video.mp4 --show-live
python analyzer.py path\to\video.mp4 --frame-skip 5
python analyzer.py path\to\video.mp4 --processing-scale 0.5
python analyzer.py path\to\video.mp4 --confidence-threshold 0.65 --held-frames 10
```

In the desktop app, use the `Resize %` numeric box next to `Show Live Processing`. `50` processes every frame at half resolution for hand detection, which is usually faster without dropping temporal information. `100` uses full resolution.

The desktop app also exposes `Confidence %` and `Held Frames`. A step is counted only when the smoothed model confidence is at or above `Confidence %` for at least `Held Frames` consecutive processed frames.

## ESP32-CAM TCP Video Capture

The project includes an ESP32-CAM sketch and a TCP video receiver for recording button-triggered camera clips directly into MP4 files.

Files:

```text
esp32cam/esp32cam.ino
videoserver_tcp.py
```

Run the TCP receiver before pressing the ESP32-CAM trigger button:

```bash
python videoserver_tcp.py --host 0.0.0.0 --port 9000 --fps 10
```

Saved videos are written to:

```text
received_videos/
```

The ESP32-CAM sketch opens one TCP connection per recording, streams JPEG frames for 15 seconds, sends an end marker, and then the Python server finalizes an MP4. This avoids SD card recording and avoids sending one HTTP request per frame.

Update these values in `esp32cam/esp32cam.ino` for your network:

```cpp
const char* ssid = "ST";
const char* password = "9876512345";
const char* serverHost = "192.168.10.131";
const uint16_t serverPort = 9000;
```

Use a unique `deviceId` for each ESP32-CAM:

```cpp
const char* deviceId = "esp32cam_01";
```

Multiple ESP32-CAM boards can stream to the same server at the same time. The server handles each TCP connection in a separate thread and names output files with the device id, for example:

```text
received_videos/20260514_185344_esp32cam_01.mp4
received_videos/20260514_185350_esp32cam_02.mp4
```

The sketch currently uses GPIO3 as the trigger input:

```text
GPIO3 ---- button ---- GND
```

GPIO3 is also UART RX, so do not hold the button while uploading/flashing code. Avoid GPIO10 for the trigger button because it is normally used by the ESP32 flash interface.

## Exported Results

Each analysis creates:

- `output/<video>_report.json`
- `output/<video>_predictions.csv`
- `output/<video>_annotated.mp4`

The report includes total completed steps, missing steps, compliance percentage, and confidence per step.

## Notes

- The app requires a trained model before analysis. Run `download_dataset.py`, `dataset_builder.py`, and `train.py` first.
- MediaPipe may output only one hand during occlusion; the feature vector preserves visibility flags and fills missing hands with zeros.
- The analyzer confirms a step only after confidence exceeds the threshold for enough consecutive frames.
- Optional future enhancement: webcam live mode.
