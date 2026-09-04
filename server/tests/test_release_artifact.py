from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from server.tests.support import DummyAuthClient, HEADERS_FILE, SESSION_COOKIE_NAME, encode_session_cookie

ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / "scripts" / "build-release.sh"
DOCKERFILE = ROOT / "Dockerfile"
ALLOWED_TOP_LEVEL_ROOTS = {"deploy", "requirements.txt", "server", "site", "wheelhouse"}
requires_docker = pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI is not available")


def runtime_python_image() -> str:
    match = re.search(
        r"python:3\.12-slim@sha256:[0-9a-f]{64}",
        DOCKERFILE.read_text(encoding="utf-8"),
    )
    assert match is not None, "Dockerfile no longer pins the Python 3.12 runtime image"
    return match.group(0)


def release_app_module(release_root: Path):
    module_path = release_root / "server" / "app.py"
    module_name = "release_server_app"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def expected_root_links() -> list[str]:
    return [
        line.strip().removeprefix("Link:").strip()
        for line in HEADERS_FILE.splitlines()
        if line.strip().startswith("Link:")
    ]


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


@pytest.fixture
def extracted_release(release: tuple[Path, str], tmp_path: Path) -> Path:
    output, sha = release
    extracted = tmp_path / "release"
    with tarfile.open(output / f"{sha}.tar.gz") as archive:
        archive.extractall(extracted, filter="data")
    return extracted


def test_release_contains_site_server_lock_and_wheelhouse(
    release: tuple[Path, str],
) -> None:
    output, sha = release
    with tarfile.open(output / f"{sha}.tar.gz") as archive:
        names = set(archive.getnames())

    top_level_roots = {name.split("/", 1)[0] for name in names}

    assert top_level_roots == ALLOWED_TOP_LEVEL_ROOTS
    assert "site/index.html" in names
    assert "site/_headers" in names
    assert "server/app.py" in names
    assert "server/main.py" in names
    assert "deploy/deploy-release.sh" in names
    assert "requirements.txt" in names
    assert any(name.startswith("wheelhouse/") and name.endswith(".whl") for name in names)


def test_release_preserves_root_link_header_behavior(extracted_release: Path) -> None:
    app_module = release_app_module(extracted_release)
    app = app_module.create_app(
        config=app_module.AppConfig(
            oidc_issuer="https://issuer.example.com",
            oidc_client_id="client-id",
            oidc_client_secret="client-secret",
            session_secret="session-secret",
            public_base_url="https://docs.example.com",
            site_dir=extracted_release / "site",
        ),
        auth_client=DummyAuthClient(),
    )
    client = TestClient(app)
    client.cookies.set(
        SESSION_COOKIE_NAME,
        encode_session_cookie(
            {"user": {"sub": "user-123"}, "authenticated_at": int(time.time())}
        ),
    )

    response = client.get("/")

    assert response.status_code == 200
    assert response.headers.get_list("link") == expected_root_links()


def test_release_checksum_matches_archive(release: tuple[Path, str]) -> None:
    output, sha = release
    archive = output / f"{sha}.tar.gz"
    expected = (output / f"{sha}.tar.gz.sha256").read_text(encoding="utf-8").split()[0]

    assert hashlib.sha256(archive.read_bytes()).hexdigest() == expected


@requires_docker
def test_release_dependencies_install_without_an_index(
    extracted_release: Path,
) -> None:
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--platform",
            "linux/amd64",
            "--volume",
            f"{extracted_release}:/release:ro",
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
