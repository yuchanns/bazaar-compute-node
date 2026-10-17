"""Compute a document report in a process that owns its file operation."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from time import time_ns
from uuid import uuid7


def main():
    request = json.loads(sys.stdin.readline())
    directory = Path(request["directory"])
    turn = request["turn"]
    envelope = {"session_id": turn["session_id"], "turn_id": turn["id"]}
    report = directory / (turn["id"] + ".json")

    def emit(kind, **values):
        print(
            json.dumps(
                {
                    "event": dict(
                        envelope,
                        kind=kind,
                        occurred_at_ms=time_ns() // 1_000_000,
                        **values,
                    )
                }
            ),
            flush=True,
        )

    try:
        emit("started")
        parts = []
        for item in turn["attachments"]:
            call = {"id": str(uuid7()), "name": "file.read", "path": item["path"]}
            emit("tool.started", metadata=call)
            content = Path(item["path"]).read_text()
            emit("tool.completed", metadata=call | {"result": content})
            parts.append(content)
        text = "\n".join(parts) or turn["input_text"]
        command = shutil.which("bcc")
        if command is None:
            raise FileNotFoundError("The harness session has no bcc command in PATH")
        statistics = subprocess.run(
            [command, "document", "count"],
            input=text,
            text=True,
            capture_output=True,
            check=True,
            encoding="utf-8",
        )
        data = {
            "title": request["title"],
            "agent_id": request["agent_id"],
            "session_id": turn["session_id"],
            **json.loads(statistics.stdout),
        }
        print(
            json.dumps(
                {
                    "approval": dict(
                        envelope,
                        request_id=turn["id"],
                        action="file_change",
                        details={"path": str(report), "report": data},
                    )
                }
            ),
            flush=True,
        )
        decision = sys.stdin.readline()
        if not decision:
            return
        if json.loads(decision)["decision"] == "approved":
            call = {"id": str(uuid7()), "name": "file.write", "path": str(report)}
            emit("tool.started", metadata=call | {"arguments": data})
            subprocess.run(
                [command, "journal", "save", str(report)],
                input=json.dumps(data),
                text=True,
                capture_output=True,
                check=True,
                encoding="utf-8",
            )
            emit("tool.completed", metadata=call | {"result": data})
            body = (
                f"Saved {data['words']} words across {data['lines']} lines to {report}."
            )
        else:
            body = "The document report was declined."
        emit("text", text=body)
        emit("completed")
    finally:
        (directory / (turn["id"] + ".closed")).write_text(str(os.getpid()))


if __name__ == "__main__":
    main()
