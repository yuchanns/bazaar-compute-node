from __future__ import annotations

from pathlib import Path

import pytest

from bazaar_compute_server.config import (
    ConfigurationError,
    ServerConfiguration,
    load_configuration,
)


def test_a_first_run_writes_the_defaults_and_reads_them_back(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"

    configuration = load_configuration(path)

    assert configuration == ServerConfiguration()
    assert path.read_text(encoding="utf-8") == (
        'listen = "127.0.0.1:8765"\nretention_days = 30\nstorage = "sqlite"\n'
    )
    assert load_configuration(path) == configuration


def test_configuration_values_are_read_and_checked(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'listen = "0.0.0.0:9000"\nretention_days = 7\nlang = "zh"\n'
        'storage = "postgres"\n\n[postgres]\ndsn = "postgresql://bcs@db/bcs"\n',
        encoding="utf-8",
    )
    configuration = load_configuration(path)
    assert configuration == ServerConfiguration(
        listen="0.0.0.0:9000",
        retention_days=7,
        lang="zh",
        storage="postgres",
        storage_options={"dsn": "postgresql://bcs@db/bcs"},
    )
    assert configuration.listen_host == "0.0.0.0"
    assert configuration.listen_port == 9000

    for content, match in (
        ('listen = "nowhere"\n', "listen"),
        ("retention_days = 0\n", "retention_days"),
        ('retention_days = "7"\n', "retention_days"),
        ('lang = ""\n', "lang"),
    ):
        path.write_text(content, encoding="utf-8")
        with pytest.raises(ConfigurationError, match=match):
            load_configuration(path)


def test_bcs_answers_version_and_help(capsys: pytest.CaptureFixture[str]) -> None:
    from bazaar_compute_server import __version__
    from bazaar_compute_server.cli import main

    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == f"bcs {__version__}"

    assert main([]) == 0
    assert capsys.readouterr().out.startswith("Usage: bcs")


def test_the_server_runs_on_the_fast_loop_and_parser(tmp_path: Path) -> None:
    import sys

    import uvicorn
    from starlette.applications import Starlette
    from uvicorn.loops.auto import auto_loop_factory

    configuration = uvicorn.Config(Starlette(), host="127.0.0.1", port=0)
    configuration.load()
    assert configuration.http_protocol_class.__module__.endswith("httptools_impl")
    if sys.platform != "win32":
        assert auto_loop_factory(use_subprocess=False).__module__ == "uvloop"


def test_the_configured_storage_comes_from_an_entry_point(tmp_path: Path) -> None:
    from bazaar_compute_server.contrib.sqlite.storage import SqliteStorage
    from bazaar_compute_server.registry import ProviderLoadError, load_storage_factory
    from bazaar_compute_server.storage import StorageContext

    storage = load_storage_factory("sqlite")(
        StorageContext(options={}, data_dir=tmp_path, retention_days=30)
    )
    assert isinstance(storage, SqliteStorage)
    assert storage.name == "sqlite"

    with pytest.raises(ProviderLoadError, match="postgres"):
        load_storage_factory("postgres")


def test_uvicorn_can_import_the_app_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib
    import sys

    import uvicorn
    from starlette.applications import Starlette

    config_path = tmp_path / "config.toml"
    config_path.write_text('listen = "127.0.0.1:8123"\n', encoding="utf-8")
    monkeypatch.setenv("BCS_CONFIG", str(config_path))
    sys.modules.pop("bazaar_compute_server.asgi", None)

    configuration = uvicorn.Config("bazaar_compute_server.asgi:app", port=8123)
    configuration.load()
    assert configuration.loaded
    module = importlib.import_module("bazaar_compute_server.asgi")
    assert isinstance(module.app, Starlette)
