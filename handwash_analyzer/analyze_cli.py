"""Subprocess-friendly analyzer entry point for the GUI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .analyzer import HandWashAnalyzer
from .utils import setup_logging


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run analysis and emit report JSON to stdout.")
    parser.add_argument("video")
    parser.add_argument("--show-live", action="store_true")
    parser.add_argument("--no-video-export", action="store_true")
    parser.add_argument("--frame-skip", type=int, default=1, help="Process every Nth frame. 1 means no skipping.")
    parser.add_argument("--processing-scale", type=float, default=1.0, help="Resize frames before hand detection. 1.0 is full size, 0.5 is half size.")
    parser.add_argument("--confidence-threshold", type=float, default=0.8, help="Minimum smoothed confidence required to count a step.")
    parser.add_argument("--held-frames", type=int, default=15, help="Minimum consecutive processed frames required to count a step.")
    return parser.parse_args()


def main() -> None:
    setup_logging()
    args = parse_args()
    analyzer = HandWashAnalyzer(
        confidence_threshold=args.confidence_threshold,
        min_duration_frames=args.held_frames,
    )
    report, _ = analyzer.analyze(
        args.video,
        show_live=args.show_live,
        export_video=not args.no_video_export,
        frame_skip=args.frame_skip,
        processing_scale=args.processing_scale,
    )
    print(json.dumps(report.to_json_dict()), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        raise
