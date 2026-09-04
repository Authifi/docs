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


def runtime_package_files() -> set[str]:
    """Every file the source `server` package is made of, minus the test suite,
    build by-products, and the requirements files that ship at the archive root.
    """
    package = ROOT / "server"

    return {
        path.relative_to(package).as_posix()
        for path in package.rglob("*")
        if path.is_file()
        and "tests" not in path.relative_to(package).parts
        and "__pycache__" not in path.parts
        and path.suffix != ".pyc"
        and not path.name.startswith("requirements")
    }


def test_the_release_carries_the_whole_runtime_server_package(
    extracted_release: Path,
) -> None:
    """Compared as sets against the source tree, because the failure mode of a
    hand-maintained file list is a module added and forgotten.

    Nothing goes wrong at build time in that case: the archive is produced, its
    checksum matches, and the offline install succeeds, because none of those
    steps import the package. It fails on the host, at the first request that
    reaches the missing module, after the candidate has already been promoted.
    """
    shipped_root = extracted_release / "server"
    shipped = {
        path.relative_to(shipped_root).as_posix()
        for path in shipped_root.rglob("*")
        if path.is_file()
    }

    assert shipped == runtime_package_files()

    # Named, so the comparison above cannot be satisfied by two empty sets.
    assert {"__init__.py", "app.py", "main.py"} <= shipped
    assert [name for name in shipped if name.startswith("tests/")] == []
    assert [name for name in shipped if "__pycache__" in name] == []


def test_the_wheelhouse_accepts_both_supported_manylinux_generations() -> None:
    """`manylinux_2_17` alone is a build that breaks the day a pinned package
    stops publishing one -- increasingly the case for anything with compiled
    extensions, which now commonly ship `manylinux_2_28` only. The target host
    is Ubuntu 24.04, whose glibc satisfies both, so accepting both costs
    nothing and removes a scheduled failure.
    """
    platforms = re.findall(r"--platform (\S+)", BUILDER.read_text(encoding="utf-8"))

    assert platforms == ["manylinux_2_17_x86_64", "manylinux_2_28_x86_64"]


def test_every_bundled_wheel_targets_the_hosts_platform(release: tuple[Path, str]) -> None:
    """The wheelhouse is installed with no index, so a wheel for the wrong
    platform is not a slow install -- it is a resolution failure on the host,
    mid-deploy, for a dependency that was present all along."""
    output, sha = release
    with tarfile.open(output / f"{sha}.tar.gz") as archive:
        wheels = [
            name
            for name in archive.getnames()
            if name.startswith("wheelhouse/") and name.endswith(".whl")
        ]

    assert wheels

    for wheel in wheels:
        for foreign in ("macosx", "win32", "win_amd64", "aarch64", "arm64", "musllinux", "i686"):
            assert foreign not in wheel, wheel


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


def test_the_release_output_directory_is_not_committable() -> None:
    """`make release` writes a seven-megabyte archive, its checksum, and a
    wheelhouse into `dist/`, and the CI job expands a runtime beside them.
    Ignored like `site/`, that output stays out of the way; untracked, it shows
    up in every `git status` and is one `git add -A` from the history.
    """
    for path in ("dist/releases/0.tar.gz", "dist/expanded/site/index.html"):
        completed = subprocess.run(
            ["git", "check-ignore", "--quiet", path], cwd=ROOT, check=False
        )

        assert completed.returncode == 0, f"{path} is not ignored"


def archiver_program() -> str:
    """The Python `build-release.sh` feeds to the interpreter to write the tar.

    Running it against a synthetic tree is what makes the properties below
    testable at all: a full release build downloads wheels from PyPI, so
    comparing two of them byte for byte would be comparing the network as much
    as the archiver.

    Selected by what it imports rather than by position, because the script
    carries more than one of these programs.
    """
    programs = [
        body
        for body in re.findall(r"(?ms)<<'PY'\n(.*?)^PY$", BUILDER.read_text(encoding="utf-8"))
        if "import tarfile" in body
    ]

    assert len(programs) == 1, f"expected one archiver program, found {len(programs)}"
    return programs[0]


def build_archive(source: Path, destination: Path) -> Path:
    completed = subprocess.run(
        [sys.executable, "-", str(source), str(destination)],
        input=archiver_program(),
        capture_output=True,
        text=True,
        env={**os.environ, "SOURCE_DATE_EPOCH": "0"},
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    return destination


def write_release_tree(root: Path, *, file_mode: int, dir_mode: int) -> None:
    """A release-shaped tree whose every entry carries the given modes."""
    (root / "site" / "assets").mkdir(parents=True)
    (root / "server").mkdir()
    (root / "wheelhouse").mkdir()

    for name, content in {
        "requirements.txt": "starlette==0.48.0\n",
        "site/index.html": "<h1>home</h1>",
        "site/assets/app.css": "body{}",
        "server/main.py": "app = object()\n",
        "wheelhouse/example-1.0-py3-none-any.whl": "not-really-a-wheel",
    }.items():
        path = root / name
        path.write_text(content, encoding="utf-8")
        path.chmod(file_mode)

    # Deepest first, and the owner keeps rwx, so the walk can still descend.
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_dir():
            path.chmod(dir_mode)


# A build machine's umask (022, 002, 077), an executable source file, and a
# world-writable one. Every one of these used to end up recorded in the archive.
MODE_VARIANTS = ((0o644, 0o755), (0o664, 0o775), (0o600, 0o700), (0o755, 0o755), (0o666, 0o777))


def test_source_modes_never_reach_the_release_archive(tmp_path: Path) -> None:
    """Two builds of one commit have to produce one archive, byte for byte.

    Permission bits were the last thing in the archive still read off the
    filesystem: uid, gid, owner names, and mtime were all pinned, and the mode
    was not. So the checksum of a release depended on the umask of whatever
    built it, and on whether anyone had ever run `chmod` in a checkout. A
    workflow rerun then reuses an existing S3 release only after its checksum
    matches, which means that dependency was a failed deploy waiting for a
    runner image to change its default umask.
    """
    digests: dict[str, tuple[int, int]] = {}

    for index, (file_mode, dir_mode) in enumerate(MODE_VARIANTS):
        source = tmp_path / f"source-{index}"
        source.mkdir()
        write_release_tree(source, file_mode=file_mode, dir_mode=dir_mode)
        archive = build_archive(source, tmp_path / f"release-{index}.tar.gz")
        digests.setdefault(
            hashlib.sha256(archive.read_bytes()).hexdigest(), (file_mode, dir_mode)
        )

    assert len(digests) == 1, {
        digest: (oct(file_mode), oct(dir_mode))
        for digest, (file_mode, dir_mode) in digests.items()
    }


def test_the_archive_records_one_mode_for_files_and_one_for_directories(
    tmp_path: Path,
) -> None:
    """The normalised values, so "identical" cannot mean "identically wrong".

    0644 leaves nothing in a release writable, including by the root installer
    that unpacks it, and 0755 on directories is what lets the service account
    traverse to the site it serves. The archive's own copy of the installer is
    provenance rather than something anything executes, so it is a file like
    any other here.
    """
    source = tmp_path / "source"
    source.mkdir()
    write_release_tree(source, file_mode=0o600, dir_mode=0o700)

    with tarfile.open(build_archive(source, tmp_path / "release.tar.gz")) as bundle:
        members = bundle.getmembers()

    assert len(members) == 9, [member.name for member in members]

    for member in members:
        assert member.mode == (0o755 if member.isdir() else 0o644), (
            f"{member.name} is {oct(member.mode)}"
        )
        assert member.uid == member.gid == 0, member.name
        assert member.uname == member.gname == "", member.name
        assert member.mtime == 0, member.name


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
