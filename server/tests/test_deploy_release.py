from __future__ import annotations

import contextlib
import errno
import fcntl
import hashlib
import json
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
    curl_args_file: Path = field(init=False)
    candidate_pid_file: Path = field(init=False)
    service_state_file: Path = field(init=False)
    lock_path: Path = field(init=False)
    fail_candidate_health_file: Path = field(init=False)
    fail_active_health_file: Path = field(init=False)
    fail_first_restart_file: Path = field(init=False)
    env: dict[str, str] = field(init=False)

    def __post_init__(self) -> None:
        self.root = self.tmp_path / "opt" / "authifi-docs"
        self.etc = self.tmp_path / "etc" / "authifi-docs"
        self.incoming_root = self.root / "incoming"
        self.releases = self.root / "releases"
        self.current = self.root / "current"
        self.fake_bin = self.tmp_path / "fake-bin"
        self.events_file = self.tmp_path / "events.log"
        self.curl_args_file = self.tmp_path / "curl-args.jsonl"
        self.candidate_pid_file = self.tmp_path / "candidate.pid"
        self.service_state_file = self.tmp_path / "service-running"
        self.lock_path = self.root / "deploy.lock"
        self.fail_candidate_health_file = self.tmp_path / "fail-candidate-health"
        self.fail_active_health_file = self.tmp_path / "fail-active-health"
        self.fail_first_restart_file = self.tmp_path / "fail-first-restart"

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
            "AUTHIFI_DOCS_CURL_CONNECT_TIMEOUT_SECONDS": "2",
            "AUTHIFI_DOCS_CURL_MAX_TIME_SECONDS": "5",
            "EVENTS_FILE": str(self.events_file),
            "CURL_ARGS_FILE": str(self.curl_args_file),
            "CANDIDATE_PID_FILE": str(self.candidate_pid_file),
            "SERVICE_STATE_FILE": str(self.service_state_file),
            "FAIL_CANDIDATE_HEALTH_FILE": str(self.fail_candidate_health_file),
            "FAIL_ACTIVE_HEALTH_FILE": str(self.fail_active_health_file),
            "FAIL_FIRST_RESTART_FILE": str(self.fail_first_restart_file),
            "PATH": f"{self.fake_bin}:{env['PATH']}",
        }

    @property
    def events(self) -> list[str]:
        if not self.events_file.exists():
            return []
        return self.events_file.read_text(encoding="utf-8").splitlines()

    @property
    def curl_invocations(self) -> list[list[str]]:
        if not self.curl_args_file.exists():
            return []
        return [json.loads(line) for line in self.curl_args_file.read_text(encoding="utf-8").splitlines()]

    @property
    def candidate_pid(self) -> int | None:
        if not self.candidate_pid_file.exists():
            return None
        return int(self.candidate_pid_file.read_text(encoding="utf-8").strip())

    @property
    def service_running(self) -> bool:
        return self.service_state_file.exists()

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

    @property
    def fail_first_restart(self) -> bool:
        return self.fail_first_restart_file.exists()

    @fail_first_restart.setter
    def fail_first_restart(self, value: bool) -> None:
        """Make the *first* `systemctl restart` fail and later ones succeed.

        A unit that can never restart is a host problem, not a deployment one.
        The interesting case is the one a rollback can still recover from: the
        candidate's unit refuses to come up, and the previous release's does.
        """
        self._set_flag(self.fail_first_restart_file, value)

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
import json
import sys
import time
from pathlib import Path

events = Path(os.environ["EVENTS_FILE"])
args_file = Path(os.environ["CURL_ARGS_FILE"])
candidate_pid_file = Path(os.environ["CANDIDATE_PID_FILE"])
url = sys.argv[-1]
with args_file.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\\n")

if ":18080/health" in url:
    deadline = time.monotonic() + 1
    while not candidate_pid_file.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
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
service_state = Path(os.environ["SERVICE_STATE_FILE"])
fail_first_restart = Path(os.environ["FAIL_FIRST_RESTART_FILE"])
event = sys.argv[1] if len(sys.argv) > 1 else "unknown"
with events.open("a", encoding="utf-8") as stream:
    stream.write(f"systemctl:{event}\\n")
if event == "restart":
    if fail_first_restart.exists():
        fail_first_restart.unlink()
        with events.open("a", encoding="utf-8") as stream:
            stream.write("systemctl:restart-failed\\n")
        sys.exit(1)
    service_state.write_text("running\\n", encoding="utf-8")
elif event == "stop":
    service_state.unlink(missing_ok=True)
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
import os
import signal
import sys
import time
from pathlib import Path

Path(os.environ["CANDIDATE_PID_FILE"]).write_text(f"{os.getpid()}\\n", encoding="utf-8")

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

    @staticmethod
    def process_exists(pid: int | None) -> bool:
        if pid is None:
            return False
        try:
            os.kill(pid, 0)
        except OSError as error:
            if error.errno == errno.ESRCH:
                return False
            raise
        return True

    def set_mtime(self, path: Path, timestamp: int) -> None:
        os.utime(path, (timestamp, timestamp), follow_symlinks=False)


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


def test_first_deploy_active_health_failure_removes_current_and_stops_service(
    deploy_harness: DeployHarness,
) -> None:
    sha = "5" * 40
    deploy_harness.publish_archive(sha)
    deploy_harness.fail_active_health_once = True

    first = deploy_harness.run(sha)

    assert first.returncode != 0
    assert not deploy_harness.current.exists()
    assert not deploy_harness.service_running
    assert "previous release restored" not in first.stderr
    assert "no previous release to restore" in first.stderr
    assert "systemctl:stop" in deploy_harness.events

    deploy_harness.fail_active_health_once = False
    retry = deploy_harness.run(sha)

    assert retry.returncode == 0
    assert "already active" not in retry.stderr
    assert deploy_harness.current.resolve().name == sha


def test_failed_service_restart_restores_the_previous_release(
    deploy_harness: DeployHarness,
) -> None:
    """`systemctl restart` failing is the same outcome as the health check
    failing, one step earlier, and it has to take the same path out.

    Under `set -e` a non-zero restart ended the installer on the spot, with
    `current` already pointing at the candidate and the previous release
    perfectly intact beside it. Nothing rolled back, nothing restarted, and the
    workflow reported a failed deploy while the host was left on a release that
    had never started -- the one state the two-stage swap exists to avoid.
    """
    old = deploy_harness.seed_active_release()
    sha = "1" * 38 + "cd"
    deploy_harness.publish_archive(sha)
    deploy_harness.fail_first_restart = True

    result = deploy_harness.run(sha)

    assert result.returncode != 0
    assert deploy_harness.current.resolve() == old
    assert deploy_harness.service_running
    assert "previous release restored" in result.stderr

    # Two restarts: the candidate's, which failed, and the previous release's.
    assert deploy_harness.events.count("systemctl:restart") == 2
    assert "systemctl:restart-failed" in deploy_harness.events

    # And the active probe never ran: there was nothing up to probe.
    assert "active-health" not in deploy_harness.events


def test_first_deploy_restart_failure_leaves_no_release_active(
    deploy_harness: DeployHarness,
) -> None:
    """With no previous release the rollback has nothing to restore, so it must
    remove `current` and stop the unit rather than leave a symlink pointing at
    a release that will not start."""
    sha = "2" * 38 + "cd"
    deploy_harness.publish_archive(sha)
    deploy_harness.fail_first_restart = True

    result = deploy_harness.run(sha)

    assert result.returncode != 0
    assert not deploy_harness.current.exists()
    assert not deploy_harness.service_running
    assert "previous release restored" not in result.stderr
    assert "no previous release to restore" in result.stderr
    assert "systemctl:stop" in deploy_harness.events


def test_the_installed_release_tree_is_never_group_or_other_writable(
    deploy_harness: DeployHarness,
) -> None:
    """The installer runs as root through Systems Manager, and everything it
    creates is read-only to the service account by construction.

    Every mode here is set explicitly rather than inherited from whatever umask
    the SSM agent happens to run with: a group-writable release directory, or
    one group-writable file inside a virtualenv, is enough for the service
    account to replace the code systemd loads on the next restart.
    """
    sha = "3" * 38 + "cd"
    deploy_harness.publish_archive(sha)

    assert deploy_harness.run(sha).returncode == 0

    release = deploy_harness.releases / sha
    tree = [
        deploy_harness.releases,
        release,
        *sorted(release.rglob("*")),
        deploy_harness.incoming_root,
    ]

    writable = [
        f"{path}: {oct(path.stat().st_mode & 0o777)}"
        for path in tree
        if not path.is_symlink() and path.stat().st_mode & 0o022
    ]

    assert writable == []
    assert len(tree) > 5, "the release tree was not actually installed"


def test_lock_prevents_concurrent_install(deploy_harness: DeployHarness) -> None:
    with deploy_harness.hold_lock():
        result = deploy_harness.run("6" * 40)

    assert result.returncode == 75
    assert "deployment already running" in result.stderr


def test_explicit_older_sha_is_a_normal_rollback(
    deploy_harness: DeployHarness,
) -> None:
    older = "7" * 40
    newer = "8" * 40
    deploy_harness.publish_archive(older)
    deploy_harness.publish_archive(newer)

    assert deploy_harness.run(newer).returncode == 0
    assert deploy_harness.run(older).returncode == 0
    assert deploy_harness.current.resolve().name == older


def test_successful_install_keeps_only_three_releases(
    deploy_harness: DeployHarness,
) -> None:
    keep = ["a" * 40, "b" * 40, "c" * 40]
    for sha in ["9" * 40, *keep]:
        deploy_harness.publish_archive(sha)
        assert deploy_harness.run(sha).returncode == 0

    assert sorted(path.name for path in deploy_harness.releases.iterdir()) == sorted(keep)


def test_health_probes_use_bounded_curl_invocation(
    deploy_harness: DeployHarness,
) -> None:
    old = deploy_harness.seed_active_release()
    deploy_harness.current.unlink()
    deploy_harness.current.symlink_to(old)
    sha = "d" * 40
    deploy_harness.publish_archive(sha)

    result = deploy_harness.run(sha)

    assert result.returncode == 0
    assert deploy_harness.curl_invocations == [
        [
            "--fail",
            "--silent",
            "--connect-timeout",
            "2",
            "--max-time",
            "5",
            "http://127.0.0.1:18080/health",
        ],
        [
            "--fail",
            "--silent",
            "--connect-timeout",
            "2",
            "--max-time",
            "5",
            "http://127.0.0.1:8080/health",
        ],
    ]


def test_pruning_preserves_unrelated_directories(
    deploy_harness: DeployHarness,
) -> None:
    preserved = deploy_harness.releases / "shared-cache"
    preserved.mkdir()
    deploy_harness.set_mtime(preserved, 10)

    for offset, sha in enumerate(["1" * 40, "2" * 40, "3" * 40], start=20):
        release = deploy_harness.make_release(sha)
        deploy_harness.set_mtime(release, offset)

    sha = "e" * 40
    deploy_harness.publish_archive(sha)

    result = deploy_harness.run(sha)

    assert result.returncode == 0
    assert preserved.is_dir()
    assert deploy_harness.current.resolve().exists()


def test_pruning_preserves_directory_symlinks(
    deploy_harness: DeployHarness,
) -> None:
    target = deploy_harness.tmp_path / "linked-target"
    target.mkdir()
    deploy_harness.set_mtime(target, 10)
    link = deploy_harness.releases / "linked-release"
    link.symlink_to(target, target_is_directory=True)

    for offset, sha in enumerate(["4" * 40, "5" * 40, "6" * 40], start=20):
        release = deploy_harness.make_release(sha)
        deploy_harness.set_mtime(release, offset)

    sha = "f" * 40
    deploy_harness.publish_archive(sha)

    result = deploy_harness.run(sha)

    assert result.returncode == 0
    assert link.is_symlink()
    assert target.exists()
    assert deploy_harness.current.resolve().exists()


def test_successful_install_stops_candidate_probe_process(
    deploy_harness: DeployHarness,
) -> None:
    old = deploy_harness.seed_active_release()
    deploy_harness.current.unlink()
    deploy_harness.current.symlink_to(old)
    sha = "1" * 39 + "a"
    deploy_harness.publish_archive(sha)

    result = deploy_harness.run(sha)

    assert result.returncode == 0
    assert deploy_harness.candidate_pid is not None
    assert not deploy_harness.process_exists(deploy_harness.candidate_pid)


def test_failed_candidate_health_stops_candidate_probe_process(
    deploy_harness: DeployHarness,
) -> None:
    deploy_harness.seed_active_release()
    sha = "1" * 39 + "b"
    deploy_harness.publish_archive(sha)
    deploy_harness.fail_candidate_health = True

    result = deploy_harness.run(sha)

    assert result.returncode != 0
    assert deploy_harness.candidate_pid is not None
    assert not deploy_harness.process_exists(deploy_harness.candidate_pid)
