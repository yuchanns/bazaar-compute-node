"""Run Pyright's language server against the files selected by pyrightconfig.json."""

from __future__ import annotations

import argparse
import json
import os
import select
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

JsonObject = dict[str, Any]

LSP_SETTINGS: JsonObject = {
    "pyright": {"disableTaggedHints": True},
    "python": {
        "analysis": {
            "autoSearchPaths": True,
            "useLibraryCodeForTypes": True,
            "diagnosticMode": "openFilesOnly",
        }
    },
}

SEVERITY_NAMES = {
    1: "error",
    2: "warning",
    3: "information",
    4: "hint",
}


class LspError(RuntimeError):
    pass


class PyrightLanguageServer:
    def __init__(self, server: str, root: Path, timeout: float) -> None:
        try:
            self.process = subprocess.Popen(
                [server, "--stdio"],
                cwd=root,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=sys.stderr,
                bufsize=0,
            )
        except OSError as error:
            raise LspError(f"cannot start {server}: {error}") from error

        stdin = self.process.stdin
        stdout = self.process.stdout
        if stdin is None or stdout is None:
            raise LspError("Pyright language server did not provide stdio pipes")

        self._stdin = stdin
        self._stdout = stdout
        self.root = root
        self.timeout = timeout
        self._next_id = 1
        self.diagnostics: dict[str, list[JsonObject]] = {}
        self.pull_results: dict[str, list[JsonObject]] = {}
        self.published_uris: set[str] = set()
        self._active_progress_tokens: set[str] = set()
        self._progress_created = False
        self._progress_started = False
        self.analysis_finished = False

    def _send(self, message: JsonObject) -> None:
        body = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        try:
            self._stdin.write(header + body)
            self._stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise LspError("Pyright language server closed its input") from error

    def notify(self, method: str, params: JsonObject | None = None) -> None:
        message: JsonObject = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)

    def request(self, method: str, params: JsonObject | None = None) -> Any:
        request_id = self._next_id
        self._next_id += 1
        message: JsonObject = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            message["params"] = params
        self._send(message)

        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LspError(f"timed out waiting for {method}")
            response = self.receive(remaining)
            if response is None:
                raise LspError(f"timed out waiting for {method}")
            if response.get("id") == request_id and (
                "result" in response or "error" in response
            ):
                if "error" in response:
                    raise LspError(f"{method} failed: {response['error']}")
                return response.get("result")
            self._handle(response)

    def receive(self, timeout: float) -> JsonObject | None:
        if self.process.poll() is not None:
            raise LspError(
                f"Pyright language server exited with status {self.process.returncode}"
            )

        ready, _, _ = select.select([self._stdout], [], [], timeout)
        if not ready:
            return None

        headers: dict[str, str] = {}
        while True:
            line = self._stdout.readline()
            if not line:
                raise LspError("Pyright language server closed its output")
            if line in (b"\r\n", b"\n"):
                break
            try:
                name, value = line.decode("ascii").split(":", 1)
            except ValueError as error:
                raise LspError(f"invalid LSP header: {line!r}") from error
            headers[name.lower()] = value.strip()

        try:
            length = int(headers["content-length"])
        except (KeyError, ValueError) as error:
            raise LspError("LSP message has no valid Content-Length header") from error

        body = self._stdout.read(length)
        if len(body) != length:
            raise LspError("LSP message body was truncated")
        try:
            message = json.loads(body)
        except json.JSONDecodeError as error:
            raise LspError("LSP message body is not valid JSON") from error
        if not isinstance(message, dict):
            raise LspError("LSP message is not a JSON object")
        return message

    def _configuration_value(self, section: str | None) -> Any:
        value: Any = LSP_SETTINGS
        if section:
            for component in section.split("."):
                if not isinstance(value, dict):
                    return None
                value = value.get(component)
        return value

    def _handle(self, message: JsonObject) -> None:
        method = message.get("method")
        params = message.get("params")

        if method == "textDocument/publishDiagnostics" and isinstance(params, dict):
            uri = params.get("uri")
            diagnostics = params.get("diagnostics", [])
            if isinstance(uri, str) and isinstance(diagnostics, list):
                self.diagnostics[uri] = diagnostics
                self.published_uris.add(uri)
            return

        if method == "$/progress" and isinstance(params, dict):
            token = params.get("token")
            value = params.get("value")
            if isinstance(token, str) and isinstance(value, dict):
                kind = value.get("kind")
                if kind == "begin":
                    self._progress_created = True
                    self._progress_started = True
                    self._active_progress_tokens.add(token)
                elif kind == "end":
                    self._active_progress_tokens.discard(token)
                    if self._progress_started and not self._active_progress_tokens:
                        self.analysis_finished = True
            return

        if method is None or "id" not in message:
            return

        request_id = message["id"]
        if method == "window/workDoneProgress/create":
            params = message.get("params")
            token = params.get("token") if isinstance(params, dict) else None
            if isinstance(token, str):
                self._progress_created = True
            self._send({"jsonrpc": "2.0", "id": request_id, "result": None})
            return
        if method == "workspace/configuration":
            items = params.get("items", []) if isinstance(params, dict) else []
            result = []
            for item in items:
                section = item.get("section") if isinstance(item, dict) else None
                result.append(self._configuration_value(section))
            self._send({"jsonrpc": "2.0", "id": request_id, "result": result})
            return

        self._send({"jsonrpc": "2.0", "id": request_id, "result": None})

    def initialize(self) -> None:
        root_uri = self.root.as_uri()
        self.request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootPath": str(self.root),
                "rootUri": root_uri,
                "workspaceFolders": [{"uri": root_uri, "name": self.root.name}],
                "capabilities": {
                    "general": {"positionEncodings": ["utf-8", "utf-16", "utf-32"]},
                    "workspace": {
                        "configuration": True,
                        "diagnostics": {"refreshSupport": True},
                        "workspaceFolders": True,
                    },
                    "textDocument": {
                        "completion": {"completionItem": {}},
                        "diagnostic": {
                            "dataSupport": True,
                            "dynamicRegistration": False,
                            "relatedDocumentSupport": True,
                            "relatedInformation": True,
                            "tagSupport": {"valueSet": [1, 2]},
                        },
                        "publishDiagnostics": {
                            "dataSupport": True,
                            "relatedInformation": True,
                            "tagSupport": {"valueSet": [1, 2]},
                        },
                    },
                    "window": {"workDoneProgress": True},
                },
                "clientInfo": {"name": "pyright-lsp-check"},
            },
        )
        self.notify("initialized", {})
        self.notify("workspace/didChangeConfiguration", {"settings": LSP_SETTINGS})

    def open_files(self, files: Sequence[Path]) -> set[str]:
        uris = set()
        for path in files:
            uri = path.resolve().as_uri()
            uris.add(uri)
            self.notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": uri,
                        "languageId": "python",
                        "version": 1,
                        "text": path.read_text(encoding="utf-8"),
                    }
                },
            )
        return uris

    def pull_diagnostics(self, uris: set[str]) -> None:
        for uri in sorted(uris):
            result = self.request(
                "textDocument/diagnostic",
                {
                    "identifier": "Pyright",
                    "textDocument": {"uri": uri},
                },
            )
            if not isinstance(result, dict):
                raise LspError(f"invalid diagnostic response for {uri}")
            kind = result.get("kind")
            if kind == "full":
                diagnostics = result.get("items", [])
                if not isinstance(diagnostics, list):
                    raise LspError(f"invalid diagnostic items for {uri}")
                self.pull_results[uri] = diagnostics
            elif kind == "unchanged":
                self.pull_results[uri] = self.diagnostics.get(uri, [])
            else:
                raise LspError(
                    f"unsupported diagnostic response kind for {uri}: {kind}"
                )

    def collect(self, expected_uris: set[str], settle_time: float) -> None:
        deadline = time.monotonic() + self.timeout
        quiet_deadline: float | None = None
        while True:
            now = time.monotonic()
            if now >= deadline:
                break
            analysis_ready = self.analysis_finished or not self._progress_created
            if analysis_ready and expected_uris.issubset(self.published_uris):
                if quiet_deadline is None:
                    quiet_deadline = min(deadline, now + settle_time)
                if now >= quiet_deadline:
                    break

            wait_until = deadline
            if quiet_deadline is not None:
                wait_until = min(wait_until, quiet_deadline)
            message = self.receive(max(0.0, wait_until - now))
            if message is None:
                break
            if message.get("method") == "$/progress":
                value = message.get("params", {}).get("value", {})
                if isinstance(value, dict) and value.get("kind") == "begin":
                    quiet_deadline = None
            self._handle(message)

    def close(self) -> None:
        if self.process.poll() is not None:
            return
        try:
            self.request("shutdown", {})
        except LspError:
            pass
        try:
            self.notify("exit")
        except LspError:
            pass
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=2)


def load_config(root: Path) -> JsonObject:
    config_path = root / "pyrightconfig.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise LspError(f"cannot read {config_path}: {error}") from error
    except json.JSONDecodeError as error:
        raise LspError(f"invalid JSON in {config_path}: {error}") from error
    if not isinstance(config, dict):
        raise LspError(f"{config_path} must contain a JSON object")
    return config


def configured_files(root: Path, config: JsonObject) -> list[Path]:
    include = config.get("include", ["."])
    if not isinstance(include, list) or not all(
        isinstance(item, str) for item in include
    ):
        raise LspError("pyrightconfig.json include must be a list of paths")

    files: set[Path] = set()
    for pattern in include:
        for match in root.glob(pattern):
            if match.is_file() and match.suffix in {".py", ".pyi"}:
                files.add(match.resolve())
            elif match.is_dir():
                files.update(
                    path.resolve()
                    for path in match.rglob("*")
                    if path.is_file() and path.suffix in {".py", ".pyi"}
                )

    excludes = config.get("exclude", [])
    excluded_roots: list[Path] = []
    if isinstance(excludes, list):
        for pattern in excludes:
            if isinstance(pattern, str):
                excluded_roots.extend(
                    match.resolve() for match in root.glob(pattern) if match.is_dir()
                )
    files = {
        path
        for path in files
        if not any(
            path == excluded or path.is_relative_to(excluded)
            for excluded in excluded_roots
        )
    }

    if not files:
        raise LspError("pyrightconfig.json selected no Python files")
    return sorted(files)


def relative_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def diagnostic_records(
    files: Sequence[Path],
    root: Path,
    diagnostics: dict[str, list[JsonObject]],
    pull_results: dict[str, list[JsonObject]],
) -> list[JsonObject]:
    records = []
    for path in files:
        uri = path.resolve().as_uri()
        records.append(
            {
                "file": relative_path(path, root),
                "diagnostics": pull_results.get(uri, diagnostics.get(uri, [])),
            }
        )
    return records


def summary(records: Sequence[JsonObject]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for record in records:
        for diagnostic in record["diagnostics"]:
            severity = SEVERITY_NAMES.get(diagnostic.get("severity"), "unknown")
            counts[severity] += 1
    return counts


def render_json(
    files: Sequence[Path], records: Sequence[JsonObject], published: set[str]
) -> None:
    counts = summary(records)
    print(
        json.dumps(
            {
                "filesAnalyzed": len(files),
                "filesPublished": len(published),
                "diagnostics": records,
                "summary": dict(counts),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def render_text(records: Sequence[JsonObject], counts: Counter[str]) -> None:
    for record in records:
        for diagnostic in record["diagnostics"]:
            start = diagnostic.get("range", {}).get("start", {})
            line = start.get("line", 0) + 1
            character = start.get("character", 0) + 1
            severity = SEVERITY_NAMES.get(diagnostic.get("severity"), "unknown")
            print(
                f"{record['file']}:{line}:{character} - {severity}: "
                f"{diagnostic.get('message', '')}"
            )
    print(
        "{} errors, {} warnings, {} informations, {} hints".format(
            counts["error"],
            counts["warning"],
            counts["information"],
            counts["hint"],
        )
    )


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Pyright's language server without an editor and fail on any diagnostic."
    )
    parser.add_argument(
        "root",
        nargs="?",
        type=Path,
        default=Path.cwd(),
        help="project root containing pyrightconfig.json (default: current directory)",
    )
    parser.add_argument(
        "--server",
        default="pyright-langserver",
        help="Pyright language-server executable (default: pyright-langserver)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="maximum seconds to wait for one analysis session (default: 60)",
    )
    parser.add_argument(
        "--settle-time",
        type=float,
        default=0.5,
        help="seconds to collect late diagnostic updates (default: 0.5)",
    )
    parser.add_argument(
        "--outputjson",
        action="store_true",
        help="emit structured JSON instead of human-readable diagnostics",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    root = args.root.resolve()
    if not root.is_dir():
        print(
            f"pyright-lsp-check: project root is not a directory: {root}",
            file=sys.stderr,
        )
        return 2
    if args.timeout <= 0 or args.settle_time < 0:
        print("pyright-lsp-check: timeout values must be positive", file=sys.stderr)
        return 2

    try:
        config = load_config(root)
        files = configured_files(root, config)
        server = PyrightLanguageServer(args.server, root, args.timeout)
        try:
            server.initialize()
            expected_uris = server.open_files(files)
            server.collect(expected_uris, args.settle_time)
            server.pull_diagnostics(expected_uris)
            records = diagnostic_records(
                files, root, server.diagnostics, server.pull_results
            )
            counts = summary(records)
            published = server.published_uris | set(server.pull_results)
            missing = expected_uris - set(server.pull_results)
            if args.outputjson:
                render_json(files, records, published)
            else:
                render_text(records, counts)
                if missing:
                    print(
                        f"{len(missing)} files did not publish diagnostics before timeout",
                        file=sys.stderr,
                    )
            if missing:
                return 2
        finally:
            server.close()
    except (LspError, OSError, UnicodeError) as error:
        print(f"pyright-lsp-check: {error}", file=sys.stderr)
        return 2

    return int(
        any(counts[level] for level in ("error", "warning", "information", "hint"))
    )


if __name__ == "__main__":
    raise SystemExit(main())
