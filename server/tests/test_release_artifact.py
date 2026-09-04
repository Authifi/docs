from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / "scripts" / "build-release.sh"
DOCKERFILE = ROOT / "Dockerfile"


def runtime_python_image() -> str:
    match = re.search(
        r"python:3\.12-slim@sha256:[0-9a-f]{64}",
        DOCKERFILE.read_text(encoding="utf-8"),
    )
    assert match is not None, "Dockerfile no longer pins the Python 3.12 runtime image"
    return match.group(0)


@pytest.fixture(scope="module")
def release(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str]:
    assert BUILDER.is_file(), f"missing release builder: {BUILDER}"

    output = tmp_path_factory.mktemp("release")
    sha = "1" * 40
    subprocess.run(
        [str(BUILDER), sha, str(output)],
        cwd=ROOT,
        check=True,
        env={**os.environ, "SOURCE_DATE_EPOCH": "0"},
    )
    return output, sha


def test_release_contains_site_server_lock_and_wheelhouse(
    release: tuple[Path, str],
) -> None:
    output, sha = release
    with tarfile.open(output / f"{sha}.tar.gz") as archive:
        names = set(archive.getnames())

    assert "site/index.html" in names
    assert "server/app.py" in names
    assert "server/main.py" in names
    assert "requirements.txt" in names
    assert any(name.startswith("wheelhouse/") and name.endswith(".whl") for name in names)


def test_release_checksum_matches_archive(release: tuple[Path, str]) -> None:
    output, sha = release
    archive = output / f"{sha}.tar.gz"
    expected = (output / f"{sha}.tar.gz.sha256").read_text(encoding="utf-8").split()[0]

    assert hashlib.sha256(archive.read_bytes()).hexdigest() == expected


def test_release_dependencies_install_without_an_index(
    release: tuple[Path, str],
    tmp_path: Path,
) -> None:
    output, sha = release
    extracted = tmp_path / "release"
    with tarfile.open(output / f"{sha}.tar.gz") as archive:
        archive.extractall(extracted, filter="data")

    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--platform",
            "linux/amd64",
            "--volume",
            f"{extracted}:/release:ro",
            runtime_python_image(),
            "sh",
            "-c",
            (
                "python -m pip install --no-cache-dir --no-index "
                "--find-links /release/wheelhouse -r /release/requirements.txt"
            ),
        ],
        check=True,
    )
