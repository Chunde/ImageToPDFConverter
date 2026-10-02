#!/usr/bin/env python3
"""Launcher for the Image to PDF Converter local web app.

Usage:
    python run.py                 # start and open the browser
    python run.py --no-browser    # start without opening a browser
    python run.py --port 9000     # prefer a specific port
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REQUIRED = {"PIL": "Pillow", "pypdf": "pypdf"}


def check_dependencies() -> list[str]:
    missing = []
    for module, package in REQUIRED.items():
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    return missing


def main() -> int:
    parser = argparse.ArgumentParser(description="Image to PDF Converter (local web app)")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="preferred port (default: 8765)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    args = parser.parse_args()

    missing = check_dependencies()
    if missing:
        print("Missing required packages: " + ", ".join(missing))
        print(f"Install them with:\n    {sys.executable} -m pip install " + " ".join(missing))
        return 1

    from app.server import serve

    serve(host=args.host, port=args.port, open_browser=not args.no_browser)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
