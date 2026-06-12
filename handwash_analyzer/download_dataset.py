"""Download the Kaggle hand-wash dataset into datasets/raw."""

from __future__ import annotations

import argparse
import logging
import shutil
import site
import subprocess
import sys
from pathlib import Path

from .utils import DATASETS_DIR, setup_logging

LOGGER = logging.getLogger(__name__)


DATASET_SLUG = "realtimear/hand-wash-dataset"


def download_dataset(output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    kaggle_exe = shutil.which("kaggle")
    if not kaggle_exe:
        scripts_exe = Path(sys.executable).resolve().parent / "Scripts" / "kaggle.exe"
        user_exe = Path(site.USER_BASE) / f"Python{sys.version_info.major}{sys.version_info.minor}" / "Scripts" / "kaggle.exe"
        if scripts_exe.exists():
            kaggle_exe = str(scripts_exe)
        elif user_exe.exists():
            kaggle_exe = str(user_exe)
    if not kaggle_exe:
        raise RuntimeError("Kaggle CLI executable was not found. Run: python -m pip install kaggle")
    command = [
        kaggle_exe,
        "datasets",
        "download",
        "-d",
        DATASET_SLUG,
        "-p",
        str(output_dir),
        "--unzip",
    ]
    LOGGER.info("Downloading Kaggle dataset %s into %s", DATASET_SLUG, output_dir)
    try:
        subprocess.run(command, check=True)
    except FileNotFoundError as exc:
        raise RuntimeError("Kaggle CLI is not installed. Run: python -m pip install kaggle") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "Kaggle download failed. Authenticate first with: kaggle auth login\n"
            "You can also use a Kaggle API token at %USERPROFILE%\\.kaggle\\kaggle.json.\n"
            "Then retry: python download_dataset.py"
        ) from exc

    videos = list(output_dir.rglob("*.mp4"))
    if not videos:
        raise RuntimeError(f"Download finished, but no .mp4 files were found under {output_dir}")
    LOGGER.info("Found %d MP4 videos after download", len(videos))
    return output_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download the Kaggle hand-wash dataset.")
    parser.add_argument("--output-dir", type=Path, default=DATASETS_DIR / "raw")
    return parser.parse_args()


def main() -> None:
    setup_logging()
    args = parse_args()
    download_dataset(args.output_dir)


if __name__ == "__main__":
    main()
