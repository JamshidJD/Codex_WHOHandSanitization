"""Application entry point."""

from __future__ import annotations

import sys

from .gui import run_app


def main() -> None:
    sys.exit(run_app())


if __name__ == "__main__":
    main()
