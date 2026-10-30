"""Restore default dependencies while testing the locally built distributions."""

import json
import os
import subprocess
import sys
import tomllib
from importlib.metadata import PackageNotFoundError, distribution
from importlib.resources import files
from pathlib import Path

import tomli_w
from packaging.requirements import Requirement
from packaging.utils import parse_sdist_filename, parse_wheel_filename

artifacts = Path(sys.argv[1])
packages: dict[str, list[Path]] = {}
for artifact in artifacts.iterdir():
    if artifact.suffix == ".whl":
        name, *_ = parse_wheel_filename(artifact.name)
    elif artifact.name.endswith(".tar.gz"):
        name, _ = parse_sdist_filename(artifact.name)
    else:
        continue
    packages.setdefault(str(name), []).append(artifact)
for name, distributions in packages.items():
    index = artifacts / "simple" / name
    index.mkdir(parents=True, exist_ok=True)
    (index / "index.html").write_text(
        "\n".join(
            f'<a href="{path.as_uri()}">{path.name}</a>' for path in distributions
        )
    )
source = (artifacts / "simple").as_uri()
document = tomllib.loads(
    files("bazaar_compute_node").joinpath("defaults.toml").read_text()
)
for package in document["contribs"]:
    package["source"] = (
        source
        if Requirement(package["package"]).name in packages
        else "https://pypi.org/simple"
    )
    for entry in package["enabled"]:
        if entry["entry"] == "contrib-manager":
            entry["config"] = {"host_source": source}
defaults = {Requirement(package["package"]).name for package in document["contribs"]}
for name in packages.keys() - defaults - {"bazaar-compute-node", "bazaar-core"}:
    direct = distribution(name).read_text("direct_url.json")
    assert direct is not None
    artifact = json.loads(direct)["url"]
    document["contribs"].append({"package": f"{name} @ {artifact}"})
for name in ("blockbuster", "pytest", "pytest-asyncio"):
    try:
        installed = distribution(name)
    except PackageNotFoundError:
        continue
    document["contribs"].append(
        {"package": f"{name}=={installed.version}", "source": "https://pypi.org/simple"}
    )
config = Path(os.environ["BCN_HOME"]) / ".bcn" / "config.toml"
config.parent.mkdir(parents=True, exist_ok=True)
config.write_text(tomli_w.dumps(document))
subprocess.run([sys.executable, "-m", "bazaar_compute_node", "--help"], check=True)
subprocess.run([sys.executable, *sys.argv[2:]], check=True)
