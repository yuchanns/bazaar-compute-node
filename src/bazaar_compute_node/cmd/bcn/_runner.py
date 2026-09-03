from __future__ import annotations

import argparse
from typing import NoReturn

import click

from ...app.usage import Usage


class UsageReporter:
    def error(self, message: str) -> NoReturn:
        raise click.UsageError(message)


def arguments(**values: object) -> argparse.Namespace:
    """Carry parsed settings to the runners that still read a namespace."""

    return argparse.Namespace(**values)


__all__ = ["Usage", "UsageReporter", "arguments"]
