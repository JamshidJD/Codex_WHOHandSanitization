"""TCP receiver for ESP32-CAM JPEG frame streams.

Protocol:
  ESPVID1\n
  {"device":"AA:BB:CC:DD:EE:FF","deviceId":"AA:BB:CC:DD:EE:FF","bleId":"tag-id","fps":10}\n
  repeated frames:
    4-byte big-endian JPEG length
    JPEG bytes
  final marker:
    4-byte big-endian zero length
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import socketserver
import struct
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 9000
DEFAULT_FPS = 10.0
DEFAULT_OUTPUT_DIR = "received_videos"
MAX_FRAME_BYTES = 2 * 1024 * 1024


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return cleaned.strip("._") or "device"


def recv_exact(sock: Any, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError("client disconnected")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def recv_line(sock: Any, limit: int = 1024) -> bytes:
    data = bytearray()
    while len(data) < limit:
        char = sock.recv(1)
        if not char:
            raise ConnectionError("client disconnected")
        if char == b"\n":
            return bytes(data)
        data.extend(char)
    raise ValueError("line too long")


@dataclass
class VideoWriterSession:
    path: Path
    fps: float
    metadata_path: Path
    metadata: dict[str, Any]
    writer: cv2.VideoWriter | None = None
    frame_size: tuple[int, int] | None = None
    frame_count: int = 0

    def write_jpeg(self, payload: bytes) -> None:
        image_data = np.frombuffer(payload, dtype=np.uint8)
        frame = cv2.imdecode(image_data, cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("payload is not a valid JPEG")

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

    def close(self) -> None:
        if self.writer is not None:
            self.writer.release()
            self.writer = None
        self.metadata["frame_count"] = self.frame_count
        self.metadata_path.write_text(json.dumps(self.metadata, indent=2), encoding="utf-8")


class ESPVideoTCPHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        server: ESPVideoTCPServer = self.server  # type: ignore[assignment]
        peer = self.client_address[0]
        session: VideoWriterSession | None = None

        try:
            magic = recv_line(self.request, 32).decode("ascii", errors="replace")
            if magic != "ESPVID1":
                raise ValueError(f"bad protocol magic: {magic!r}")

            metadata = json.loads(recv_line(self.request, 512).decode("utf-8"))
            device = safe_name(str(metadata.get("deviceId") or metadata.get("device") or peer))
            ble_id = str(metadata.get("bleId") or metadata.get("BLEID") or "none")
            fps = float(metadata.get("fps") or server.fps)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = server.output_dir / f"{timestamp}_{device}.mp4"
            session_metadata = {
                "timestamp": timestamp,
                "deviceId": str(metadata.get("deviceId") or metadata.get("device") or peer),
                "bleId": ble_id,
                "fps": fps,
                "peer": peer,
                "video_path": str(path),
            }
            session = VideoWriterSession(
                path=path,
                fps=fps,
                metadata_path=path.with_suffix(".json"),
                metadata=session_metadata,
            )

            logging.info(
                "TCP stream started device=%s bleId=%s peer=%s file=%s fps=%s",
                device,
                ble_id,
                peer,
                path,
                fps,
            )

            while True:
                frame_size = struct.unpack(">I", recv_exact(self.request, 4))[0]
                if frame_size == 0:
                    break
                if frame_size > MAX_FRAME_BYTES:
                    raise ValueError(f"frame too large: {frame_size}")
                session.write_jpeg(recv_exact(self.request, frame_size))

            self.request.sendall(b"OK\n")
            logging.info("TCP stream complete file=%s frames=%s", session.path, session.frame_count)

        except Exception:
            logging.exception("TCP stream failed peer=%s", peer)
            try:
                self.request.sendall(b"ERR\n")
            except OSError:
                pass
        finally:
            if session is not None:
                session.close()


class ESPVideoTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], output_dir: Path, fps: float) -> None:
        super().__init__(server_address, ESPVideoTCPHandler)
        self.output_dir = output_dir
        self.fps = fps
        self.output_dir.mkdir(parents=True, exist_ok=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Receive ESP32-CAM TCP frame streams and save MP4 files.")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"bind host, default {DEFAULT_HOST}")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"bind port, default {DEFAULT_PORT}")
    parser.add_argument("--fps", type=float, default=DEFAULT_FPS, help=f"default output FPS, default {DEFAULT_FPS}")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(DEFAULT_OUTPUT_DIR),
        help=f"directory for saved videos, default {DEFAULT_OUTPUT_DIR}",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    with ESPVideoTCPServer((args.host, args.port), args.output_dir, args.fps) as server:
        logging.info("Listening for TCP streams on %s:%s", args.host, args.port)
        logging.info("Saving videos to %s", args.output_dir.resolve())
        server.serve_forever()


if __name__ == "__main__":
    main()
