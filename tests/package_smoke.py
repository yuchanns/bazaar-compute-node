from __future__ import annotations

import subprocess
import sys
from importlib.metadata import version

from bazaar_compute_node import __version__


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: package_smoke.py VERSION")

    expected_version = sys.argv[1]
    distribution_version = version("bazaar-compute-node")
    result = subprocess.run(
        ["bcn", "--version"],
        capture_output=True,
        check=True,
        text=True,
    )

    if distribution_version != expected_version:
        raise SystemExit(
            f"distribution version {distribution_version!r} != {expected_version!r}"
        )
    if __version__ != expected_version:
        raise SystemExit(f"runtime version {__version__!r} != {expected_version!r}")
    if result.stdout.strip() != f"bcn {expected_version}":
        raise SystemExit(f"unexpected bcn --version output: {result.stdout!r}")


if __name__ == "__main__":
    main()
