from handwash_analyzer.analyzer import HandWashAnalyzer
from handwash_analyzer.utils import setup_logging


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Analyze an MP4 for WHO hand hygiene compliance.")
    parser.add_argument("video", help="Path to .mp4 file")
    parser.add_argument("--show-live", action="store_true")
    parser.add_argument("--no-video-export", action="store_true")
    parser.add_argument("--frame-skip", type=int, default=1, help="Process every Nth frame. 1 means no skipping.")
    parser.add_argument("--processing-scale", type=float, default=1.0, help="Resize frames before hand detection. 1.0 is full size, 0.5 is half size.")
    parser.add_argument("--confidence-threshold", type=float, default=0.8, help="Minimum smoothed confidence required to count a step.")
    parser.add_argument("--held-frames", type=int, default=15, help="Minimum consecutive processed frames required to count a step.")
    args = parser.parse_args()
    setup_logging()
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
    print(f"Compliance Score: {report.compliance_percentage:.1f}%")
    print("Completed:", ", ".join(report.completed_steps) or "None")
    print("Missing:", ", ".join(report.missing_steps) or "None")


if __name__ == "__main__":
    main()
