"""Point this node at a bazaar compute server."""

from __future__ import annotations

import argparse
import os
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

from ..contrib.server.audit import endpoint
from .config import (
    ConfigurationError,
    NodeConfiguration,
    _replace_file,
    _write_configuration,
    load_node_configuration,
    resolve_config_path,
)
from .system_service import default_env_file, installed_env_file
from .usage import Usage

TOKEN_ENV = "BCN_SERVER_TOKEN"


def run_server_command(args: argparse.Namespace, parser: Usage) -> int:
    if (
        args.storage is not None
        or args.audit is not None
        or args.database_name is not None
        or args.endpoint is not None
        or args.foreground
    ):
        parser.error("bcn server commands only accept the node-level --config option")
    config_path = (args.config or resolve_config_path()).expanduser()
    try:
        configuration = load_node_configuration(config_path)
    except ConfigurationError as error:
        parser.error(str(error))
    match args.server_command:
        case "connect":
            return _connect(args, parser, configuration, config_path)
        case unsupported:
            raise AssertionError(f"unsupported server command: {unsupported}")


def _connect(
    args: argparse.Namespace,
    parser: Usage,
    configuration: NodeConfiguration,
    config_path: Path,
) -> int:
    url = args.url.rstrip("/")
    token = args.token
    if endpoint(url) is None:
        parser.error("--url must be an absolute http(s) URL")
    if not token:
        parser.error("--token must not be empty")
    # a registered service already said which file it reads; before any
    # registration the default is ours to set, and the install must match it
    registered = installed_env_file()
    env_file = (args.env_file or registered or default_env_file()).expanduser()
    updated = replace(
        configuration,
        audit="server",
        audit_options=MappingProxyType({"url": url, "token_env": TOKEN_ENV}),
    )
    # the token goes first: a configuration that names the server sink is
    # only right once the credential it reads exists
    _write_env_value(env_file, TOKEN_ENV, token)
    try:
        _write_configuration(config_path, updated)
    except ConfigurationError as error:
        parser.error(str(error))
    print(f"Connected to {url}", flush=True)
    if registered is None and args.env_file is None:
        print(
            "Register the service with `bcn system-service install`, which"
            " reads this file, then `bcn system-service start`.",
            flush=True,
        )
    else:
        print("Run `bcn system-service restart` to apply.", flush=True)
    return 0


def _write_env_value(path: Path, name: str, value: str) -> None:
    """Set one variable in the service's environment file, keeping the rest."""

    line = f"$env:{name} = '{value}'" if os.name == "nt" else f"{name}={value}"
    prefix = f"$env:{name} " if os.name == "nt" else f"{name}="
    kept = []
    if path.exists():
        kept = [
            existing
            for existing in path.read_text(encoding="utf-8").splitlines()
            if not existing.startswith(prefix)
        ]
    path.parent.mkdir(parents=True, exist_ok=True)
    # the file also holds the channel credentials: it is replaced whole, never
    # left half written
    _replace_file(path, "\n".join((*kept, line)) + "\n")


__all__ = ["TOKEN_ENV", "default_env_file", "run_server_command"]
