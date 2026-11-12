#!/usr/bin/env python3
"""Run real-cluster tests; pytest arguments may be used to select tests."""

from __future__ import annotations

import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent


def main() -> int:
    return subprocess.call(["uv", "run", "--locked", "pytest", *sys.argv[1:]], cwd=HERE)


if __name__ == "__main__":
    sys.exit(main())
