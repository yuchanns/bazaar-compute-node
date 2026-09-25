"""`bcn` with the test plugins, for a test that runs a node in a process of
its own."""

from __future__ import annotations

import sys

from bazaar_compute_node.cli import main

from .plugin import install

if __name__ == "__main__":
    install()
    sys.exit(main(sys.argv[1:]))
