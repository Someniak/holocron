"""Guards for the container build's dependency and package-integrity controls.

Both the public and internal images install from the same uv.lock, and the
internal APK install must verify package signatures. These tests fail if either
control is weakened by a future Dockerfile or CI edit.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _dockerfile() -> str:
    return (ROOT / "Dockerfile").read_text(encoding="utf-8")


def test_dockerfile_installs_from_the_single_lock_with_hashes():
    dockerfile = _dockerfile()

    assert "uv export --frozen" in dockerfile
    assert "--require-hashes" in dockerfile
    assert "--no-build-isolation" in dockerfile
    # The old build-path escape hatches must not come back.
    assert "UV_LOCK_MODE" not in dockerfile
    assert "UV_LOCK_FILE" not in dockerfile
    assert "uv sync" not in dockerfile


def test_build_backend_is_a_locked_dependency_group():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "hatchling" in pyproject["dependency-groups"]["build"]

    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    assert "hatchling" in {package["name"] for package in lock["package"]}


def test_internal_apk_install_verifies_package_signatures():
    dockerfile = _dockerfile()

    # Fetching may use the unsigned internal index; installing may not.
    assert "apk fetch --allow-untrusted" in dockerfile
    assert "apk add --no-network --no-cache /tmp/apk-packages/*.apk" in dockerfile

    install_line = next(
        line
        for line in dockerfile.splitlines()
        if "apk add --no-network --no-cache /tmp/apk-packages/*.apk" in line
    )
    assert "--allow-untrusted" not in install_line
