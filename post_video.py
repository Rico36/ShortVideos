#!/usr/bin/env python3
"""Entry point: ./post_video.py check | post  (see social/README.md)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from socialpost.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
