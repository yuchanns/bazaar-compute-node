"""Publish a completed document report after its scheduled delay."""

import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path


def main():
    report = Path(sys.argv[1])
    while not report.with_suffix(".closed").exists():
        time.sleep(0.02)
    time.sleep(float(sys.argv[2]))
    data = json.loads(report.read_text())
    target = re.search(r"\[target=(.*?) msg=", data["inbox"])
    assert target is not None
    command = shutil.which("bcc")
    if command is None:
        raise FileNotFoundError("The harness session has no bcc command in PATH")
    history = subprocess.run(
        [command, "message", "read", "--target", target[1]],
        text=True,
        capture_output=True,
        check=True,
        encoding="utf-8",
    )
    temporary = report.with_suffix(".publication.tmp")
    temporary.write_text(
        json.dumps(
            {
                "report": data,
                "history": history.stdout,
                "published_at_ns": time.time_ns(),
            }
        )
    )
    temporary.replace(report.with_suffix(".published.json"))


if __name__ == "__main__":
    main()
