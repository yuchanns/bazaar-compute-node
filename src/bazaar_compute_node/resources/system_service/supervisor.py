# Managed by bazaar-compute-node.
"""Keep the service's running entry point separate from its installed entry point."""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path
from types import FrameType


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    running = Path(__file__).with_name(
        "bcn.running.exe" if os.name == "nt" else "bcn.running"
    )
    stopping = False
    process: subprocess.Popen[bytes] | None = None

    def stop(number: int, frame: FrameType | None) -> None:
        nonlocal stopping
        del number, frame
        stopping = True
        if process is not None and process.poll() is None:
            process.terminate()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    while not stopping:
        try:
            shutil.copy2(args.executable, running)
            if stopping:
                break
            process = subprocess.Popen(
                [str(running), "run", "--config", str(args.config)],
                stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            print(f"system service supervisor started pid={process.pid}", flush=True)
            if stopping:
                process.terminate()
            code = process.wait()
            print(f"system service supervisor child exited code={code}", flush=True)
        except OSError as error:
            print(f"system service supervisor launch failed: {error}", flush=True)
        if not stopping:
            time.sleep(1)


if __name__ == "__main__":
    main()
