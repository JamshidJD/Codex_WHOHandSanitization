"""HTTP video receiver for ESP32-CAM JPEG frame uploads.

The companion ESP sketch should call:

    GET  /start?device=<device-id>
    POST /frame?device=<device-id>
    GET  /stop?device=<device-id>   # optional
    POST /stop?device=<device-id>   # optional

If no device id is sent, the server uses the ESP's source IP address. Each
device can have one active recording at a time, and multiple devices can upload
concurrently.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000
DEFAULT_FPS = 10.0
DEFAULT_OUTPUT_DIR = "received_videos"
DEFAULT_IDLE_TIMEOUT_SECONDS = 30.0
MAX_FRAME_BYTES = 2 * 1024 * 1024


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return cleaned.strip("._") or "device"


@dataclass
class RecordingSession:
    device_id: str
    path: Path
    fps: float
    started_at: float
    last_frame_at: float
    frame_count: int = 0
    writer: cv2.VideoWriter | None = None
    frame_size: tuple[int, int] | None = None

    def write_jpeg(self, payload: bytes) -> None:
        image_data = np.frombuffer(payload, dtype=np.uint8)
        frame = cv2.imdecode(image_data, cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("payload is not a valid JPEG image")

        height, width = frame.shape[:2]
        if self.writer is None:
            self.frame_size = (width, height)
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            self.writer = cv2.VideoWriter(str(self.path), fourcc, self.fps, self.frame_size)
            if not self.writer.isOpened():
                raise RuntimeError(f"could not open video writer for {self.path}")

        if self.frame_size != (width, height):
            frame = cv2.resize(frame, self.frame_size, interpolation=cv2.INTER_AREA)

        self.writer.write(frame)
        self.frame_count += 1
        self.last_frame_at = time.time()

    def close(self) -> None:
        if self.writer is not None:
            self.writer.release()
            self.writer = None


class SessionStore:
    def __init__(self, output_dir: Path, fps: float, idle_timeout: float) -> None:
        self.output_dir = output_dir
        self.fps = fps
        self.idle_timeout = idle_timeout
        self.lock = threading.RLock()
        self.sessions: dict[str, RecordingSession] = {}
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def start(self, device_id: str) -> RecordingSession:
        with self.lock:
            old_session = self.sessions.pop(device_id, None)
            if old_session is not None:
                old_session.close()

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = self.output_dir / f"{timestamp}_{safe_name(device_id)}.mp4"
            session = RecordingSession(
                device_id=device_id,
                path=path,
                fps=self.fps,
                started_at=time.time(),
                last_frame_at=time.time(),
            )
            self.sessions[device_id] = session
            return session

    def get_or_start(self, device_id: str) -> RecordingSession:
        with self.lock:
            session = self.sessions.get(device_id)
            if session is None:
                session = self.start(device_id)
            return session

    def stop(self, device_id: str) -> RecordingSession | None:
        with self.lock:
            session = self.sessions.pop(device_id, None)
            if session is not None:
                session.close()
            return session

    def cleanup_idle(self) -> None:
        now = time.time()
        with self.lock:
            idle_devices = [
                device_id
                for device_id, session in self.sessions.items()
                if now - session.last_frame_at > self.idle_timeout
            ]
            for device_id in idle_devices:
                session = self.sessions.pop(device_id)
                session.close()
                logging.info(
                    "Closed idle session device=%s frames=%s file=%s",
                    device_id,
                    session.frame_count,
                    session.path,
                )

    def close_all(self) -> None:
        with self.lock:
            sessions = list(self.sessions.values())
            self.sessions.clear()
        for session in sessions:
            session.close()


class VideoUploadHandler(BaseHTTPRequestHandler):
    server_version = "ESPVideoServer/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/start":
            device_id = self.device_id(parsed)
            session = self.server.session_store.start(device_id)  # type: ignore[attr-defined]
            logging.info("Started session device=%s file=%s", device_id, session.path)
            self.send_json({"ok": True, "device": device_id, "file": str(session.path)})
            return

        if parsed.path == "/health":
            self.send_json({"ok": True})
            return

        if parsed.path == "/stop":
            self.handle_stop(parsed)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "unknown endpoint")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/frame":
            self.handle_frame(parsed)
            return

        if parsed.path == "/stop":
            self.handle_stop(parsed)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "unknown endpoint")

    def handle_stop(self, parsed: Any) -> None:
        device_id = self.device_id(parsed)
        session = self.server.session_store.stop(device_id)  # type: ignore[attr-defined]
        if session is None:
            self.send_json({"ok": True, "device": device_id, "message": "no active session"})
            return

        logging.info(
            "Stopped session device=%s frames=%s file=%s",
            device_id,
            session.frame_count,
            session.path,
        )
        self.send_json(
            {
                "ok": True,
                "device": device_id,
                "frames": session.frame_count,
                "file": str(session.path),
            }
        )

    def handle_frame(self, parsed: Any) -> None:
        content_length = self.headers.get("Content-Length")
        if not content_length:
            self.send_error(HTTPStatus.LENGTH_REQUIRED, "missing Content-Length")
            return

        try:
            frame_size = int(content_length)
        except ValueError:
            self.send_error(HTTPStatus.BAD_REQUEST, "invalid Content-Length")
            return

        if frame_size <= 0 or frame_size > MAX_FRAME_BYTES:
            self.send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "invalid frame size")
            return

        payload = self.rfile.read(frame_size)
        if len(payload) != frame_size:
            self.send_error(HTTPStatus.BAD_REQUEST, "incomplete frame payload")
            return

        device_id = self.device_id(parsed)
        session = self.server.session_store.get_or_start(device_id)  # type: ignore[attr-defined]

        try:
            with self.server.session_store.lock:  # type: ignore[attr-defined]
                session.write_jpeg(payload)
        except ValueError as exc:
            self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except Exception:
            logging.exception("Failed to write frame for device=%s", device_id)
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "failed to write frame")
            return

        self.send_json(
            {
                "ok": True,
                "device": device_id,
                "frame": session.frame_count,
                "file": str(session.path),
            }
        )

    def device_id(self, parsed: Any) -> str:
        query = parse_qs(parsed.query)
        device = query.get("device", [None])[0] or self.headers.get("X-Device-Id")
        if device:
            return safe_name(device)
        return safe_name(self.client_address[0])

    def send_json(self, payload: dict[str, Any], status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        logging.info("%s - %s", self.client_address[0], fmt % args)


class VideoHTTPServer(ThreadingHTTPServer):
    session_store: SessionStore


def idle_cleanup_loop(store: SessionStore, stop_event: threading.Event) -> None:
    while not stop_event.wait(5.0):
        store.cleanup_idle()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Receive ESP32-CAM JPEG frames and save MP4 files.")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"bind host, default {DEFAULT_HOST}")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"bind port, default {DEFAULT_PORT}")
    parser.add_argument("--fps", type=float, default=DEFAULT_FPS, help=f"output video FPS, default {DEFAULT_FPS}")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(DEFAULT_OUTPUT_DIR),
        help=f"directory for saved videos, default {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--idle-timeout",
        type=float,
        default=DEFAULT_IDLE_TIMEOUT_SECONDS,
        help="seconds without frames before a recording is finalized",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    store = SessionStore(args.output_dir, args.fps, args.idle_timeout)
    server = VideoHTTPServer((args.host, args.port), VideoUploadHandler)
    server.session_store = store
    stop_event = threading.Event()
    cleanup_thread = threading.Thread(target=idle_cleanup_loop, args=(store, stop_event), daemon=True)
    cleanup_thread.start()

    logging.info("Listening on http://%s:%s", args.host, args.port)
    logging.info("Saving videos to %s", args.output_dir.resolve())

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Shutting down")
    finally:
        stop_event.set()
        server.server_close()
        store.close_all()


if __name__ == "__main__":
    main()
