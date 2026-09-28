"""SIMULATED third-party MCP server, "package-registry". This is NOT a real
external service -- there was no live package-registry MCP server available
to actually attach to, so this is a small, honestly-labeled stand-in backed
by package_registry_data.json, written by us. It's analyzed in risk_note.md
as if it were a genuine third party we don't control, which is the correct
way to reason about supply-chain risk regardless of who actually wrote the
code -- the exercise is about the TRUST BOUNDARY a second server crosses,
not about this specific file's contents.

Exposes 3 tools: get_package_version, get_release_date, check_package_deprecated.

Run standalone:
    python3 package_registry_server.py
"""

import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP

DATA_PATH = Path(__file__).parent / "package_registry_data.json"
_DATA = json.loads(DATA_PATH.read_text())
_PACKAGES = {k: v for k, v in _DATA.items() if not k.startswith("_")}

mcp = FastMCP(name="package-registry")


def _package_error(name: str) -> dict:
    return {
        "found": False,
        "error": (
            f"no package named '{name}' in this registry. Known packages: "
            f"{', '.join(sorted(_PACKAGES.keys()))}. This registry only covers packages related "
            f"to this project's docs (GitHub/Swagger tooling) -- it is not a general npm/PyPI mirror."
        ),
    }


def _version_error(name: str, version: str) -> dict:
    known = sorted(_PACKAGES[name]["versions"].keys())
    return {
        "found": False,
        "error": (
            f"package '{name}' has no version '{version}' on record. Known versions: "
            f"{', '.join(known)} (latest: {_PACKAGES[name]['latest_version']})."
        ),
    }


@mcp.tool()
def get_package_version(name: str) -> dict:
    """Look up the latest known version of a package by name.

    Use this first if you don't yet know which specific version to ask
    check_package_deprecated or get_release_date about.
    """
    if name not in _PACKAGES:
        return _package_error(name)
    return {"found": True, "name": name, "latest_version": _PACKAGES[name]["latest_version"]}


@mcp.tool()
def get_release_date(name: str, version: str) -> dict:
    """Look up the release date of one specific (package, version) pair.

    Does not say whether that version is deprecated -- call
    check_package_deprecated for that.
    """
    if name not in _PACKAGES:
        return _package_error(name)
    if version not in _PACKAGES[name]["versions"]:
        return _version_error(name, version)
    return {"found": True, "name": name, "version": version,
            "release_date": _PACKAGES[name]["versions"][version]["release_date"]}


@mcp.tool()
def check_package_deprecated(name: str, version: str) -> dict:
    """Check whether one specific (package, version) pair is deprecated, and
    if so, return the deprecation note explaining what to upgrade to.

    Recoverable by design: an unknown package or version returns a message
    naming the packages/versions that ARE known, not a bare "not found" --
    so the model can tell "this doesn't exist" apart from "the registry is
    down," per the Week 9 brief's own warning about that exact failure mode.
    """
    if name not in _PACKAGES:
        return _package_error(name)
    if version not in _PACKAGES[name]["versions"]:
        return _version_error(name, version)
    info = _PACKAGES[name]["versions"][version]
    return {
        "found": True, "name": name, "version": version,
        "deprecated": info["deprecated"],
        "deprecation_note": info.get("deprecation_note"),
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
