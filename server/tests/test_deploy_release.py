from __future__ import annotations

import contextlib
import fcntl
import hashlib
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOYER = ROOT / "infra" / "scripts" / "deploy-release.sh"


@dataclass
class DeployHarness:
    tmp_path: Path
    root: Path = field(init=False)
    etc: Path = field(init=False)
    incoming_root: Path = field(init=False)
    releases: Path = field(init=False)
    current: Path = field(init=False)
    fake_bin: Path = field(init=False)
    events_file: Path = field(init=False)
    lock_path: Path = field(init=False)
    fail_candidate_health_file: Path = field(init=False)
    fail_active_health_file: Path = field(init=False)
    env: dict[str, str] = field(init=False)

    def __post_init__(self) -> None:
        self.root = self.tmp_path / "opt" / "authifi-docs"
        self.etc = self.tmp_path / "etc" / "authifi-docs"
        self.incoming_root = self.root / "incoming"
        self.releases = self.root / "releases"
        self.current = self.root / "current"
        self.fake_bin = self.tmp_path / "fake-bin"
        self.events_file = self.tmp_path / "events.log"
        self.lock_path = self.root / "deploy.lock"
        self.fail_candidate_health_file = self.tmp_path / "fail-candidate-health"
        self.fail_active_health_file = self.tmp_path / "fail-active-health"

        self.releases.mkdir(parents=True)
        self.incoming_root.mkdir(parents=True)
        self.etc.mkdir(parents=True)
        self.fake_bin.mkdir(parents=True)
        (self.etc / "environment").write_text("AUTHIFI_ENV=test\n", encoding="utf-8")
        (self.etc / "session.env").write_text("SESSION_NAME=test\n", encoding="utf-8")
        self._write_fake_commands()

        env = os.environ.copy()
        self.env = {
            **env,
            "AUTHIFI_DOCS_ROOT": str(self.root),
            "AUTHIFI_DOCS_ETC": str(self.etc),
            "AUTHIFI_DOCS_LOCK": str(self.lock_path),
            "AUTHIFI_DOCS_PYTHON_BIN": sys.executable,
            "AUTHIFI_DOCS_UVICORN_BIN": str(self.fake_bin / "uvicorn"),
            "AUTHIFI_DOCS_CURL_BIN": str(self.fake_bin / "curl"),
            "AUTHIFI_DOCS_SYSTEMCTL_BIN": str(self.fake_bin / "systemctl"),
            "AUTHIFI_DOCS_TIMEOUT_BIN": str(self.fake_bin / "timeout"),
            "AUTHIFI_DOCS_CANDIDATE_HEALTH_ATTEMPTS": "2",
            "AUTHIFI_DOCS_ACTIVE_HEALTH_ATTEMPTS": "1",
            "AUTHIFI_DOCS_HEALTH_SLEEP_SECONDS": "0",
            "EVENTS_FILE": str(self.events_file),
            "FAIL_CANDIDATE_HEALTH_FILE": str(self.fail_candidate_health_file),
            "FAIL_ACTIVE_HEALTH_FILE": str(self.fail_active_health_file),
            "PATH": f"{self.fake_bin}:{env['PATH']}",
        }

    @property
    def events(self) -> list[str]:
        if not self.events_file.exists():
            return []
        return self.events_file.read_text(encoding="utf-8").splitlines()

    @property
    def fail_candidate_health(self) -> bool:
        return self.fail_candidate_health_file.exists()

    @fail_candidate_health.setter
    def fail_candidate_health(self, value: bool) -> None:
        self._set_flag(self.fail_candidate_health_file, value)

    @property
    def fail_active_health_once(self) -> bool:
        return self.fail_active_health_file.exists()

    @fail_active_health_once.setter
    def fail_active_health_once(self, value: bool) -> None:
        self._set_flag(self.fail_active_health_file, value)

    def _set_flag(self, path: Path, value: bool) -> None:
        if value:
            path.write_text("1\n", encoding="utf-8")
            return
        path.unlink(missing_ok=True)

    def _write_fake_commands(self) -> None:
        self._write_executable(
            self.fake_bin / "curl",
            """#!/usr/bin/env python3
import os
import sys
from pathlib import Path

events = Path(os.environ["EVENTS_FILE"])
url = sys.argv[-1]
if ":18080/health" in url:
    event = "candidate-health"
    should_fail = Path(os.environ["FAIL_CANDIDATE_HEALTH_FILE"]).exists()
elif ":8080/health" in url:
    event = "active-health"
    should_fail = Path(os.environ["FAIL_ACTIVE_HEALTH_FILE"]).exists()
else:
    print(f"unexpected curl target: {url}", file=sys.stderr)
    sys.exit(2)

with events.open("a", encoding="utf-8") as stream:
    stream.write(f"{event}\\n")

sys.exit(22 if should_fail else 0)
""",
        )
        self._write_executable(
            self.fake_bin / "systemctl",
            """#!/usr/bin/env python3
import os
import sys
from pathlib import Path

events = Path(os.environ["EVENTS_FILE"])
event = sys.argv[1] if len(sys.argv) > 1 else "unknown"
with events.open("a", encoding="utf-8") as stream:
    stream.write(f"systemctl:{event}\\n")
sys.exit(0)
""",
        )
        self._write_executable(
            self.fake_bin / "timeout",
            """#!/usr/bin/env python3
import os
import sys

if len(sys.argv) < 3:
    print("usage: timeout SECONDS COMMAND...", file=sys.stderr)
    sys.exit(2)

os.execvp(sys.argv[2], sys.argv[2:])
""",
        )
        self._write_executable(
            self.fake_bin / "flock",
            """#!/usr/bin/env python3
import fcntl
import os
import sys

if len(sys.argv) < 4 or sys.argv[1] != "-n":
    print("usage: flock -n LOCKFILE COMMAND...", file=sys.stderr)
    sys.exit(2)

lock_path = sys.argv[2]
command = sys.argv[3:]

fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
try:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(1)
    os.execvp(command[0], command)
finally:
    os.close(fd)
""",
        )
        self._write_executable(
            self.fake_bin / "uvicorn",
            """#!/usr/bin/env python3
import signal
import sys
import time

def stop(_signum, _frame):
    sys.exit(0)

signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)

while True:
    time.sleep(1)
""",
        )

    def _write_executable(self, path: Path, body: str) -> None:
        path.write_text(body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def make_release(self, sha: str) -> Path:
        release = self.releases / sha
        (release / "site").mkdir(parents=True, exist_ok=True)
        (release / "server").mkdir(parents=True, exist_ok=True)
        (release / "wheelhouse").mkdir(parents=True, exist_ok=True)
        (release / "requirements.txt").write_text("", encoding="utf-8")
        (release / "site" / "index.html").write_text(f"<h1>{sha}</h1>\n", encoding="utf-8")
        (release / "server" / "__init__.py").write_text("", encoding="utf-8")
        (release / "server" / "main.py").write_text("app = object()\n", encoding="utf-8")
        return release

    def seed_active_release(self) -> Path:
        current_release = self.make_release("0" * 40)
        self.current.symlink_to(current_release)
        return current_release

    def publish_archive(self, sha: str, checksum: str | None = None) -> Path:
        incoming = self.incoming_root / sha
        incoming.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory() as staging_dir:
            staged = Path(staging_dir) / "release"
            self._build_release_tree(staged, sha)
            archive = incoming / f"{sha}.tar.gz"
            with tarfile.open(archive, "w:gz") as bundle:
                bundle.add(staged, arcname=".")

        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        recorded = checksum or digest
        (incoming / f"{sha}.tar.gz.sha256").write_text(
            f"{recorded}  {sha}.tar.gz\n",
            encoding="utf-8",
        )
        return archive

    def _build_release_tree(self, root: Path, sha: str) -> None:
        (root / "site").mkdir(parents=True)
        (root / "server").mkdir()
        (root / "wheelhouse").mkdir()
        (root / "requirements.txt").write_text("", encoding="utf-8")
        (root / "site" / "index.html").write_text(f"<h1>{sha}</h1>\n", encoding="utf-8")
        (root / "server" / "__init__.py").write_text("", encoding="utf-8")
        (root / "server" / "main.py").write_text("app = object()\n", encoding="utf-8")

    def run(self, sha: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(DEPLOYER), sha],
            cwd=ROOT,
            capture_output=True,
            text=True,
            env=self.env,
        )

    @contextlib.contextmanager
    def hold_lock(self) -> Iterator[None]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("w", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@pytest.fixture
def deploy_harness(tmp_path: Path) -> DeployHarness:
    return DeployHarness(tmp_path)


def test_successful_install_switches_current_only_after_candidate_health(
    deploy_harness: DeployHarness,
) -> None:
    old = deploy_harness.make_release("0" * 40)
    deploy_harness.current.symlink_to(old)
    new_sha = "1" * 40
    deploy_harness.publish_archive(new_sha)

    result = deploy_harness.run(new_sha)

    assert result.returncode == 0
    assert deploy_harness.current.resolve().name == new_sha
    assert deploy_harness.events.index("candidate-health") < deploy_harness.events.index(
        "systemctl:restart"
    )
    assert "active-health" in deploy_harness.events


def test_bad_checksum_preserves_current(deploy_harness: DeployHarness) -> None:
    old = deploy_harness.seed_active_release()
    sha = "2" * 40
    deploy_harness.publish_archive(sha, checksum="0" * 64)

    assert deploy_harness.run(sha).returncode != 0
    assert deploy_harness.current.resolve() == old


def test_failed_candidate_preserves_current(deploy_harness: DeployHarness) -> None:
    old = deploy_harness.seed_active_release()
    sha = "3" * 40
    deploy_harness.publish_archive(sha)
    deploy_harness.fail_candidate_health = True

    assert deploy_harness.run(sha).returncode != 0
    assert deploy_harness.current.resolve() == old


def test_failed_active_health_restores_previous_release(
    deploy_harness: DeployHarness,
) -> None:
    old = deploy_harness.seed_active_release()
    sha = "4" * 40
    deploy_harness.publish_archive(sha)
    deploy_harness.fail_active_health_once = True

    assert deploy_harness.run(sha).returncode != 0
    assert deploy_harness.current.resolve() == old
    assert deploy_harness.events.count("systemctl:restart") == 2


def test_lock_prevents_concurrent_install(deploy_harness: DeployHarness) -> None:
    with deploy_harness.hold_lock():
        result = deploy_harness.run("5" * 40)

    assert result.returncode == 75
    assert "deployment already running" in result.stderr


def test_explicit_older_sha_is_a_normal_rollback(
    deploy_harness: DeployHarness,
) -> None:
    older = "6" * 40
    newer = "7" * 40
    deploy_harness.publish_archive(older)
    deploy_harness.publish_archive(newer)

    assert deploy_harness.run(newer).returncode == 0
    assert deploy_harness.run(older).returncode == 0
    assert deploy_harness.current.resolve().name == older


def test_successful_install_keeps_only_three_releases(
    deploy_harness: DeployHarness,
) -> None:
    keep = ["9" * 40, "a" * 40, "b" * 40]
    for sha in ["8" * 40, *keep]:
        deploy_harness.publish_archive(sha)
        assert deploy_harness.run(sha).returncode == 0

    assert sorted(path.name for path in deploy_harness.releases.iterdir()) == sorted(keep)
