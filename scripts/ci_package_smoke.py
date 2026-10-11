"""Verify a distribution installed outside its source workspace."""

import importlib
import subprocess
import sys
from importlib.metadata import distribution
from pathlib import PurePosixPath


def main() -> None:
    name, version, license, *smoke = sys.argv[1:]
    installed = distribution(name)
    if installed.version != version:
        raise RuntimeError(f"{name}: installed version differs from {version}")
    if installed.metadata["License-Expression"] != license:
        raise RuntimeError(f"{name}: installed license differs from {license}")
    files = installed.files or []
    licenses = [file for file in files if ".dist-info/licenses/" in str(file)]
    if not licenses or any(
        not installed.locate_file(file).read_bytes() for file in licenses
    ):
        raise RuntimeError(f"{name}: installed license files are missing or empty")

    modules: set[str] = set()
    for file in files:
        path = PurePosixPath(str(file))
        if path.suffix == ".py":
            module = path.parts[0] if len(path.parts) > 1 else path.stem
            if module.isidentifier():
                modules.add(module)
    for module in sorted(modules):
        importlib.import_module(module)
    if smoke:
        subprocess.run([sys.executable, smoke[0], version], check=True)
    print(f"Installed {name} {version}: imports and {license} license verified")


if __name__ == "__main__":
    main()
