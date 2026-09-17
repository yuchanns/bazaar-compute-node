from __future__ import annotations

from pathlib import Path

import click
import uvicorn

from . import __version__
from .app import create_app
from .config import ConfigurationError, load_configuration, resolve_data_dir


@click.group("bcs", context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(
    __version__, "--version", prog_name="bcs", message="%(prog)s %(version)s"
)
def bcs() -> None:
    """Bazaar compute server: the management plane for bcn nodes."""


@bcs.command("run", help="Run the server in the foreground.")
@click.option(
    "--config",
    type=click.Path(path_type=Path),
    help="Configuration file; defaults to ~/.bcs/config.toml.",
)
def run(config: Path | None) -> None:
    try:
        configuration = load_configuration(config)
    except ConfigurationError as failure:
        raise click.UsageError(str(failure)) from failure
    # the app is built here from the configuration just read and handed
    # over whole; uvicorn still builds the loop itself
    uvicorn.run(
        create_app(configuration, resolve_data_dir()),
        host=configuration.listen_host,
        port=configuration.listen_port,
        log_level="info",
    )


def main(arguments: list[str] | None = None) -> int:
    try:
        bcs.main(args=arguments, prog_name="bcs", standalone_mode=False)
    except SystemExit as exit_error:
        return int(exit_error.code or 0)
    except click.exceptions.NoArgsIsHelpError as error:
        click.echo((error.ctx or click.Context(bcs, info_name="bcs")).get_help())
        return 0
    except click.ClickException as error:
        error.show()
        raise SystemExit(error.exit_code) from None
    except click.Abort:
        return 1
    return 0


__all__ = ["bcs", "main"]
