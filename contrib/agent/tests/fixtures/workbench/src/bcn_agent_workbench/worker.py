"""Compute a document report in a process that owns its file operation."""

import json
import os
import re
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
        command = shutil.which("bcc")
        if command is None:
            raise FileNotFoundError("The harness session has no bcc command in PATH")
        inbox = subprocess.run(
            [command, "message", "check"],
            text=True,
            capture_output=True,
            check=True,
            encoding="utf-8",
        )
        prompt = (
            inbox.stdout.rsplit("New messages:", 1)[-1]
            if "[inbox notice:" in turn["input_text"]
            else turn["input_text"]
        )
        if "[inbox notice:" in turn["input_text"] and "[target=" not in prompt:
            row = re.search(
                r"^([^\n ]+)  pending:.*latest msg=([0-9a-f-]+)",
                turn["input_text"],
                re.MULTILINE,
            )
            if row is not None:
                history = subprocess.run(
                    [
                        command,
                        "message",
                        "read",
                        "--target",
                        row[1],
                        "--limit",
                        "100",
                    ],
                    text=True,
                    capture_output=True,
                    check=True,
                    encoding="utf-8",
                )
                headers = list(
                    re.finditer(
                        r"^\[\d+/\d+ seq=[^\]\n]*type=(?:human|system)[^\]\n]*\]",
                        history.stdout,
                        re.MULTILINE,
                    )
                )
                if headers:
                    source = history.stdout[headers[-1].start() :].split("\n[", 1)[0]
                    prompt = re.sub(
                        r"\[[^\]\n]* msg=([^ \]]+)[^\]\n]* replyTarget=([^ \]]+)[^\]\n]*\]",
                        r"[target=\2 msg=\1]",
                        source,
                    )
        paths = [item["path"] for item in turn["attachments"]] or re.findall(
            r"path:([^\n)]+)", prompt
        )
        parts = []
        for path in paths:
            path = str(Path(path).absolute())
            call = {"id": str(uuid7()), "name": "file.read", "path": path}
            emit("tool.started", metadata=call)
            content = Path(path).read_text()
            emit("tool.completed", metadata=call | {"result": content})
            parts.append(content)
        text = "\n".join(parts) or prompt
        statistics = subprocess.run(
            [command, "document", "count"],
            input=text,
            text=True,
            capture_output=True,
            check=True,
            encoding="utf-8",
        )
        header = re.search(r"\[target=(.*?) msg=([^ ]+)", prompt)
        if header is None:
            raise ValueError("The conversation message has no reply address")
        target, message_id = header.groups()
        data = {
            "title": request["title"],
            "editor": os.environ.get("EDITOR_NAME"),
            "style": request.get("style", ""),
            "inbox": inbox.stdout,
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
        while True:
            decision = sys.stdin.readline()
            if not decision:
                return
            decision = json.loads(decision)
            if "steer" not in decision:
                break
            accepted = request["input_mode"] == "interactive" and decision[
                "input_text"
            ].startswith("[inbox notice:")
            print(
                json.dumps({"steer": decision["steer"], "accepted": accepted}),
                flush=True,
            )
            if accepted:
                update = subprocess.run(
                    [command, "message", "check"],
                    text=True,
                    capture_output=True,
                    check=True,
                    encoding="utf-8",
                )
                with (directory / (turn["id"] + ".steered.jsonl")).open("a") as stream:
                    stream.write(
                        json.dumps(
                            {
                                "notice": decision["input_text"],
                                "inbox": update.stdout,
                                "at_ns": time_ns(),
                            }
                        )
                        + "\n"
                    )
                emit("inbox.checked", metadata={"messages": update.stdout})
        if decision["decision"] == "approved":
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
        if request["input_mode"] == "batch":
            emit("text", text=body)
            emit("completed")
            return
        checked = subprocess.run(
            [command, "message", "check"],
            text=True,
            capture_output=True,
            check=True,
            encoding="utf-8",
        )
        (directory / (turn["id"] + ".inbox.json")).write_text(
            json.dumps({"inbox": checked.stdout})
        )
        subprocess.run(
            [command, "message", "send", "--target", target],
            input=body,
            text=True,
            capture_output=True,
            check=True,
            encoding="utf-8",
        )
        if "remind me" in prompt.casefold():
            subprocess.run(
                [
                    command,
                    "reminder",
                    "schedule",
                    "--delay-seconds",
                    "1",
                    "--title",
                    "Send the draft to the editor",
                    "--message-id",
                    message_id,
                ],
                text=True,
                capture_output=True,
                check=True,
                encoding="utf-8",
            )
        emit("text", text=body)
        emit("completed")
    except subprocess.CalledProcessError as error:
        print(error.stdout or "", file=sys.stderr)
        print(error.stderr or "", file=sys.stderr)
        raise
    finally:
        (directory / (turn["id"] + ".closed")).write_text(str(os.getpid()))


if __name__ == "__main__":
    main()
