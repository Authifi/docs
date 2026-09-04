from __future__ import annotations

import contextlib
import errno
import fcntl
import hashlib
import json
import os
import re
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOYER = ROOT / "infra" / "scripts" / "deploy-release.sh"

# Exactly what `local.host_config` puts in `/etc/authifi-docs/config.json`.
# The installer requires this key set and no other, so this is not just a
# fixture -- it is the contract, and the tests below vary it one key at a time.
HOST_CONFIGURATION = {
    "AWS_REGION": "us-east-1",
    "OIDC_ISSUER": "https://issuer.authifi.io/tenants/authifi",
    "OIDC_CLIENT_ID": "authifi-docs",
    "OIDC_CLIENT_SECRET_PARAMETER_NAME": "/authifi-docs/oidc-client-secret",
    "PUBLIC_BASE_URL": "https://docs.authifi.io",
    "SITE_DIR": "/opt/authifi-docs/current/site",
    "POST_LOGOUT_PATH": "/logged-off",
}


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
    setpriv_args_file: Path = field(init=False)
    candidate_pid_file: Path = field(init=False)
    uvicorn_env_file: Path = field(init=False)
    env_args_file: Path = field(init=False)
    service_state_file: Path = field(init=False)
    lock_path: Path = field(init=False)
    fail_candidate_health_file: Path = field(init=False)
    fail_active_health_file: Path = field(init=False)
    fail_first_restart_file: Path = field(init=False)
    test_pause_hold_file: Path = field(init=False)
    test_pause_marker_file: Path = field(init=False)
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
        self.setpriv_args_file = self.tmp_path / "setpriv-args.jsonl"
        self.candidate_pid_file = self.tmp_path / "candidate.pid"
        self.uvicorn_env_file = self.tmp_path / "candidate-env.json"
        self.env_args_file = self.tmp_path / "env-args.jsonl"
        self.service_state_file = self.tmp_path / "service-running"
        self.lock_path = self.root / "deploy.lock"
        self.fail_candidate_health_file = self.tmp_path / "fail-candidate-health"
        self.fail_active_health_file = self.tmp_path / "fail-active-health"
        self.fail_first_restart_file = self.tmp_path / "fail-first-restart"
        self.test_pause_hold_file = self.tmp_path / "test-pause-hold"
        self.test_pause_marker_file = self.tmp_path / "test-pause-marker"

        self.releases.mkdir(parents=True)
        self.incoming_root.mkdir(parents=True)
        self.etc.mkdir(parents=True)
        self.fake_bin.mkdir(parents=True)
        self.write_configuration(HOST_CONFIGURATION)
        (self.etc / "session.env").write_text(
            "SESSION_SECRET=0123456789abcdef\n", encoding="utf-8"
        )
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
            "AUTHIFI_DOCS_SETPRIV_BIN": str(self.fake_bin / "setpriv"),
            "AUTHIFI_DOCS_ENV_BIN": str(self.fake_bin / "env"),
            "AUTHIFI_DOCS_CANDIDATE_HEALTH_ATTEMPTS": "2",
            "AUTHIFI_DOCS_ACTIVE_HEALTH_ATTEMPTS": "1",
            "AUTHIFI_DOCS_HEALTH_SLEEP_SECONDS": "0",
            "AUTHIFI_DOCS_CURL_CONNECT_TIMEOUT_SECONDS": "2",
            "AUTHIFI_DOCS_CURL_MAX_TIME_SECONDS": "5",
            "EVENTS_FILE": str(self.events_file),
            "CURL_ARGS_FILE": str(self.curl_args_file),
            "SETPRIV_ARGS_FILE": str(self.setpriv_args_file),
            "CANDIDATE_PORT": "18080",
            "CANDIDATE_PID_FILE": str(self.candidate_pid_file),
            "UVICORN_ENV_FILE": str(self.uvicorn_env_file),
            "ENV_ARGS_FILE": str(self.env_args_file),
            "SERVICE_STATE_FILE": str(self.service_state_file),
            "FAIL_CANDIDATE_HEALTH_FILE": str(self.fail_candidate_health_file),
            "FAIL_ACTIVE_HEALTH_FILE": str(self.fail_active_health_file),
            "FAIL_FIRST_RESTART_FILE": str(self.fail_first_restart_file),
            "AUTHIFI_DOCS_TEST_PAUSE_POINT": "after_swap",
            "AUTHIFI_DOCS_TEST_PAUSE_HOLD": str(self.test_pause_hold_file),
            "AUTHIFI_DOCS_TEST_PAUSE_MARKER": str(self.test_pause_marker_file),
            "PATH": f"{self.fake_bin}:{env['PATH']}",
        }

    def write_configuration(self, configuration: dict[str, str]) -> None:
        """The strict JSON the bootstrap writes for the installer to parse."""
        (self.etc / "config.json").write_text(
            json.dumps(configuration), encoding="utf-8"
        )

    @property
    def candidate_environment(self) -> dict[str, str]:
        """The environment the candidate server was actually started with."""
        if not self.uvicorn_env_file.exists():
            return {}
        return json.loads(self.uvicorn_env_file.read_text(encoding="utf-8"))

    @property
    def candidate_environment_order(self) -> list[str]:
        """The names root passed to `env`, in the order it passed them."""
        if not self.env_args_file.exists():
            return []
        last = json.loads(
            self.env_args_file.read_text(encoding="utf-8").splitlines()[-1]
        )
        return [assignment.partition("=")[0] for assignment in last]

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
    def setpriv_invocations(self) -> list[list[str]]:
        if not self.setpriv_args_file.exists():
            return []
        return [
            json.loads(line)
            for line in self.setpriv_args_file.read_text(encoding="utf-8").splitlines()
        ]

    @property
    def candidate_port(self) -> int:
        return int(self.env["CANDIDATE_PORT"])

    @candidate_port.setter
    def candidate_port(self, value: int) -> None:
        """Move the candidate probe port, for both the installer and the fake curl.

        Tests that need a port genuinely occupied ask the OS for one rather than
        contending for 18080, which something on a developer's machine may
        already be using.
        """
        self.env["AUTHIFI_DOCS_CANDIDATE_PORT"] = str(value)
        self.env["CANDIDATE_PORT"] = str(value)

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
candidate_port = os.environ["CANDIDATE_PORT"]
url = sys.argv[-1]
with args_file.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\\n")

if f":{candidate_port}/health" in url:
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

os.execv(sys.argv[2], sys.argv[2:])
""",
        )
        # Records the assignments in the order root passed them, then behaves
        # like `env`. The order is only observable here: once `env` has applied
        # them the child sees a dictionary, and the argument list is what an
        # operator reads in an SSM log.
        self._write_executable(
            self.fake_bin / "env",
            """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

arguments = sys.argv[1:]
assignments = []
while arguments and "=" in arguments[0]:
    assignments.append(arguments.pop(0))

with Path(os.environ["ENV_ARGS_FILE"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(assignments) + "\\n")

if not arguments:
    print("env: no command given", file=sys.stderr)
    sys.exit(2)

for assignment in assignments:
    name, _, value = assignment.partition("=")
    os.environ[name] = value

os.execv(arguments[0], arguments)
""",
        )
        # Records the privilege drop and then execs, the way real setpriv does.
        # Rejecting a malformed invocation is the point: if the installer stops
        # passing `--reuid`/`--regid` or drops the `--`, the candidate probe
        # fails here rather than quietly running as root.
        self._write_executable(
            self.fake_bin / "setpriv",
            """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

arguments = sys.argv[1:]
with Path(os.environ["SETPRIV_ARGS_FILE"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(arguments) + "\\n")

if "--" not in arguments:
    print("usage: setpriv [options] -- COMMAND...", file=sys.stderr)
    sys.exit(2)

command = arguments[arguments.index("--") + 1 :]
if not command:
    print("setpriv: no command given", file=sys.stderr)
    sys.exit(2)

os.execvp(command[0], command)
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
import json
import os
import signal
import sys
import time
from pathlib import Path

# What the installer handed the candidate server, which is the only thing that
# says whether the configuration survived being parsed rather than sourced.
Path(os.environ["UVICORN_ENV_FILE"]).write_text(
    json.dumps(
        {
            name: value
            for name, value in os.environ.items()
            if name in ("AWS_REGION", "OIDC_ISSUER", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET_PARAMETER_NAME",
                        "PUBLIC_BASE_URL", "SITE_DIR", "POST_LOGOUT_PATH", "SESSION_SECRET",
                        "AUTHIFI_ENV", "SESSION_NAME")
        }
    ),
    encoding="utf-8",
)
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

    def make_python_venv_failure_shim(self) -> Path:
        """An interpreter whose `-m venv` fails and whose everything else works.

        The installer uses this same binary for the lock wrapper, the checksum
        check, the port probe, the symlink swap, and the prune, so failing the
        whole interpreter would not reach the step under test.
        """
        shim = self.fake_bin / "python-venv-failure-shim"
        self._write_executable(
            shim,
            f"""#!/usr/bin/env python3
import subprocess
import sys

if sys.argv[1:3] == ["-m", "venv"]:
    print("could not create a virtualenv", file=sys.stderr)
    sys.exit(1)

sys.exit(subprocess.run([{sys.executable!r}, *sys.argv[1:]]).returncode)
""",
        )
        return shim

    def make_python_venv_activation_copymode_shim(self) -> Path:
        """Simulate CPython's template-mode copy leaking write bits into a venv.

        The real bug was not "our Python happened to do this today", it was the
        installer trusting whatever modes `python -m venv` left behind. This
        shim keeps the test deterministic by delegating to the real interpreter
        and then widening the activation scripts after a successful venv
        creation.
        """
        shim = self.fake_bin / "python-venv-copymode-shim"
        self._write_executable(
            shim,
            f"""#!/usr/bin/env python3
import stat
import subprocess
import sys
from pathlib import Path

command = [{sys.executable!r}, *sys.argv[1:]]
completed = subprocess.run(command)

if completed.returncode == 0 and len(sys.argv) >= 4 and sys.argv[1:3] == ["-m", "venv"]:
    for activate in Path(sys.argv[3]).glob("bin/activate*"):
        if activate.is_file():
            activate.chmod(activate.stat().st_mode | stat.S_IWGRP | stat.S_IWOTH)

sys.exit(completed.returncode)
""",
        )
        return shim

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

    def publish_archive(
        self,
        sha: str,
        checksum: str | None = None,
        requirements: str = "",
    ) -> Path:
        incoming = self.incoming_root / sha
        incoming.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory() as staging_dir:
            staged = Path(staging_dir) / "release"
            self._build_release_tree(staged, sha, requirements)
            archive = incoming / f"{sha}.tar.gz"
            with tarfile.open(archive, "w:gz") as bundle:
                bundle.add(staged, arcname=".")

        self._record_checksum(incoming, sha, checksum or self._digest_of(archive))
        return archive

    def publish_unextractable_archive(self, sha: str) -> Path:
        """An archive whose checksum is right and whose contents are not a tar.

        `aws:downloadContent` verifies nothing, so a truncated download or a
        corrupted object reaches the installer looking exactly like this: the
        checksum step passes, `releases/<sha>` is created, and `tar` fails.
        """
        incoming = self.incoming_root / sha
        incoming.mkdir(parents=True, exist_ok=True)
        archive = incoming / f"{sha}.tar.gz"
        archive.write_bytes(b"this is not a gzip stream")
        self._record_checksum(incoming, sha, self._digest_of(archive))
        return archive

    @staticmethod
    def _digest_of(archive: Path) -> str:
        return hashlib.sha256(archive.read_bytes()).hexdigest()

    @staticmethod
    def _record_checksum(incoming: Path, sha: str, digest: str) -> None:
        (incoming / f"{sha}.tar.gz.sha256").write_text(
            f"{digest}  {sha}.tar.gz\n",
            encoding="utf-8",
        )

    def _build_release_tree(self, root: Path, sha: str, requirements: str = "") -> None:
        (root / "site").mkdir(parents=True)
        (root / "server").mkdir()
        (root / "wheelhouse").mkdir()
        (root / "requirements.txt").write_text(requirements, encoding="utf-8")
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

    # Re-staged, because the failed run cleared its own staging directory. This
    # is what Systems Manager does too: `aws:downloadContent` runs ahead of the
    # installer on every invocation, so a retry never depends on what the last
    # attempt left on disk.
    deploy_harness.publish_archive(sha)
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
    deploy_harness.env["AUTHIFI_DOCS_PYTHON_BIN"] = str(
        deploy_harness.make_python_venv_activation_copymode_shim()
    )

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


def stage_unrelated_deployment(harness: DeployHarness) -> Path:
    """Another SHA's staged archive, which no exit path may remove.

    Clearing `incoming` wholesale would be a correct-looking fix that deletes
    an archive Systems Manager staged for a deployment this run knows nothing
    about.
    """
    other = "7" * 39 + "e"
    harness.publish_archive(other)
    return harness.incoming_root / other


def test_a_successful_install_clears_only_its_own_staging_directory(
    deploy_harness: DeployHarness,
) -> None:
    other = stage_unrelated_deployment(deploy_harness)
    sha = "4" * 38 + "cd"
    deploy_harness.publish_archive(sha)

    assert deploy_harness.run(sha).returncode == 0
    assert not (deploy_harness.incoming_root / sha).exists()
    assert other.is_dir()


def test_a_rejected_checksum_clears_the_staging_directory_it_refused(
    deploy_harness: DeployHarness,
) -> None:
    """Only the success path used to clear staging, so every failure left an
    archive and its wheelhouse on the root volume until somebody noticed.
    Nothing in there is worth keeping -- the same bytes are in S3 under the
    same SHA -- and the one this test stages is a *rejected* archive.
    """
    deploy_harness.seed_active_release()
    other = stage_unrelated_deployment(deploy_harness)
    sha = "5" * 38 + "cd"
    deploy_harness.publish_archive(sha, checksum="0" * 64)

    assert deploy_harness.run(sha).returncode != 0
    assert not (deploy_harness.incoming_root / sha).exists()
    assert other.is_dir()


def test_an_unhealthy_candidate_clears_its_staging_directory(
    deploy_harness: DeployHarness,
) -> None:
    deploy_harness.seed_active_release()
    sha = "6" * 38 + "cd"
    deploy_harness.publish_archive(sha)
    deploy_harness.fail_candidate_health = True

    assert deploy_harness.run(sha).returncode != 0
    assert not (deploy_harness.incoming_root / sha).exists()


def test_an_already_active_release_clears_its_staging_directory(
    deploy_harness: DeployHarness,
) -> None:
    """The early exit returns zero, so this path looked like a success and left
    a full staged archive behind on every redeploy of the current release."""
    sha = "8" * 38 + "cd"
    deploy_harness.publish_archive(sha)

    assert deploy_harness.run(sha).returncode == 0

    deploy_harness.publish_archive(sha)
    result = deploy_harness.run(sha)

    assert result.returncode == 0
    assert "already active" in result.stderr
    assert not (deploy_harness.incoming_root / sha).exists()


# --- What a failed deployment leaves in the release tree ----------------------
#
# `releases/<sha>` is created before anything is extracted into it, so every
# failure between there and activation left a release tree behind: extracted,
# virtualenv and all. Repeated failures on the same commit accumulated them,
# and the next successful prune counted them as recent releases -- keeping
# incomplete trees by mtime while deleting the known-good release a rollback
# would have needed.


@dataclass
class FailedDeployment:
    """A deployment that fails at one named step, and what has to survive it."""

    harness: DeployHarness
    sha: str
    previous: Path
    unrelated: Path
    result: subprocess.CompletedProcess[str]

    @property
    def candidate(self) -> Path:
        return self.harness.releases / self.sha


def deploy_that_fails(
    harness: DeployHarness,
    sha: str,
    arrange: Callable[[DeployHarness, str], None],
) -> FailedDeployment:
    """Run one deployment arranged to fail, alongside releases it must not touch."""
    previous = harness.seed_active_release()
    unrelated = harness.make_release("3" * 39 + "f")
    arrange(harness, sha)

    result = harness.run(sha)

    return FailedDeployment(harness, sha, previous, unrelated, result)


def stage_corrupt_archive(harness: DeployHarness, sha: str) -> None:
    harness.publish_unextractable_archive(sha)


def stage_failing_venv(harness: DeployHarness, sha: str) -> None:
    harness.publish_archive(sha)
    harness.env["AUTHIFI_DOCS_PYTHON_BIN"] = str(harness.make_python_venv_failure_shim())


def stage_uninstallable_requirements(harness: DeployHarness, sha: str) -> None:
    # `--no-index --find-links wheelhouse`, and the wheelhouse is empty, so pip
    # has nowhere to resolve this from. A release built without its wheels is
    # exactly this failure.
    harness.publish_archive(sha, requirements="a-package-no-wheelhouse-has==1.0\n")


def stage_occupied_candidate_port(harness: DeployHarness, sha: str) -> None:
    harness.publish_archive(sha)
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    harness.candidate_port = holder.getsockname()[1]
    # Held for the rest of the test, and closed when the process exits.
    harness.port_holder = holder  # type: ignore[attr-defined]


def stage_unhealthy_candidate(harness: DeployHarness, sha: str) -> None:
    harness.publish_archive(sha)
    harness.fail_candidate_health = True


def stage_failing_restart(harness: DeployHarness, sha: str) -> None:
    harness.publish_archive(sha)
    harness.fail_first_restart = True


def stage_unhealthy_active_release(harness: DeployHarness, sha: str) -> None:
    harness.publish_archive(sha)
    harness.fail_active_health_once = True


FAILURE_STEPS: dict[str, Callable[[DeployHarness, str], None]] = {
    "extraction": stage_corrupt_archive,
    "virtualenv": stage_failing_venv,
    "dependency-install": stage_uninstallable_requirements,
    "candidate-port": stage_occupied_candidate_port,
    "candidate-health": stage_unhealthy_candidate,
    "service-restart": stage_failing_restart,
    "active-health": stage_unhealthy_active_release,
}


@pytest.mark.parametrize("step", sorted(FAILURE_STEPS))
def test_a_failed_deployment_leaves_no_release_tree_behind(
    deploy_harness: DeployHarness, step: str
) -> None:
    """Every failure, before the swap and after it, and the same outcome.

    The two post-swap failures roll `current` back to the previous release
    first, which is what makes the abandoned candidate removable by the same
    rule as the pre-activation ones: it is not what the host is serving.
    """
    failed = deploy_that_fails(deploy_harness, "c" * 38 + "de", FAILURE_STEPS[step])

    assert failed.result.returncode != 0, failed.result.stdout
    assert not failed.candidate.exists(), f"{step} left {failed.candidate}"


@pytest.mark.parametrize("step", sorted(FAILURE_STEPS))
def test_cleaning_up_a_failed_deployment_touches_nothing_else(
    deploy_harness: DeployHarness, step: str
) -> None:
    """Removing the candidate is only safe if it is *only* the candidate.

    `rm -rf` over the release tree, or over `incoming`, would be a
    correct-looking fix that deletes the release the host is serving or an
    archive Systems Manager staged for a deployment this run knows nothing
    about.
    """
    other_staging = stage_unrelated_deployment(deploy_harness)
    failed = deploy_that_fails(deploy_harness, "c" * 38 + "de", FAILURE_STEPS[step])

    assert failed.result.returncode != 0
    assert deploy_harness.current.resolve() == failed.previous
    assert (failed.previous / "site" / "index.html").is_file()
    assert failed.unrelated.is_dir()
    assert other_staging.is_dir()
    assert not (deploy_harness.incoming_root / failed.sha).exists()


def test_repeated_failures_never_become_releases_a_prune_would_keep(
    deploy_harness: DeployHarness,
) -> None:
    """The consequence the accumulated trees actually had.

    `prune_releases` keeps the two most recently modified non-active
    directories whose names look like a SHA. Three failed attempts left three
    such directories, all newer than the release a rollback needed, so the next
    successful deployment pruned the good one and kept the wreckage.
    """
    rollback_target = "1" * 40
    deploy_harness.publish_archive(rollback_target)
    assert deploy_harness.run(rollback_target).returncode == 0

    live = "2" * 40
    deploy_harness.publish_archive(live)
    assert deploy_harness.run(live).returncode == 0

    for digit in "345":
        failed_sha = digit * 40
        deploy_harness.publish_unextractable_archive(failed_sha)
        assert deploy_harness.run(failed_sha).returncode != 0

    successor = "6" * 40
    deploy_harness.publish_archive(successor)

    assert deploy_harness.run(successor).returncode == 0
    assert sorted(path.name for path in deploy_harness.releases.iterdir()) == sorted(
        [rollback_target, live, successor]
    )
    assert deploy_harness.current.resolve().name == successor


def wait_for_path(path: Path, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.02)
    raise AssertionError(f"{path} never appeared")


def signal_during_deploy(
    deploy_harness: DeployHarness,
    sha: str,
    *,
    signal_name: str = "SIGTERM",
    pause_point: str = "after_swap",
) -> subprocess.CompletedProcess[str]:
    """Drive a deployment to a named pause point, then signal it."""
    import signal as signal_module

    number = getattr(signal_module, signal_name)
    deploy_harness.test_pause_hold_file.write_text("1\n", encoding="utf-8")

    env = {**deploy_harness.env, "AUTHIFI_DOCS_TEST_PAUSE_POINT": pause_point}
    process = subprocess.Popen(
        [str(DEPLOYER), sha],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    wait_for_path(deploy_harness.test_pause_marker_file)
    process.send_signal(number)
    deploy_harness.test_pause_hold_file.unlink(missing_ok=True)
    stdout, stderr = process.communicate(timeout=60)
    return subprocess.CompletedProcess(
        process.args,
        process.returncode,
        stdout,
        stderr,
    )


@pytest.mark.parametrize("signal_name", ["SIGHUP", "SIGINT", "SIGTERM"])
def test_a_signalled_deployment_before_swap_cleans_up_and_reports_the_signal(
    deploy_harness: DeployHarness, signal_name: str
) -> None:
    """Systems Manager cancels a command by signalling the installer, and a
    workflow run cancelled mid-deploy is the normal way that happens.

    `trap ... EXIT` alone does not cover it: bash runs the EXIT trap when the
    shell exits, but a default-disposition SIGTERM kills it without one, so a
    cancelled deploy left both the staged archive and a half-built release
    tree behind -- the same accumulation the prune bug fed on.

    The status has to survive too. A handler that returns zero would turn a
    killed deployment into a successful one as far as Systems Manager and the
    workflow are concerned.
    """
    import signal as signal_module

    number = getattr(signal_module, signal_name)
    sha = "a" * 37 + "bcd"
    deploy_harness.publish_archive(sha, requirements="# slow\n")
    deploy_harness.seed_active_release()

    process = subprocess.Popen(
        [str(DEPLOYER), sha],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=deploy_harness.env,
    )
    candidate = deploy_harness.releases / sha
    deadline = time.monotonic() + 30
    while not candidate.exists() and time.monotonic() < deadline:
        if process.poll() is not None:
            break
        time.sleep(0.02)

    assert candidate.exists(), "the candidate tree was never created to clean up"

    process.send_signal(number)
    _, stderr = process.communicate(timeout=60)

    assert process.returncode != 0, stderr
    # 128 + signal is what a shell killed by one reports, and it is what
    # Systems Manager needs to see rather than a plain 1 or a 0.
    assert process.returncode in (128 + number, -number), process.returncode
    assert not candidate.exists(), "a cancelled deployment left its release tree"
    assert not (deploy_harness.incoming_root / sha).exists()
    assert deploy_harness.current.resolve().name == "0" * 40
    # Once. The EXIT trap fires on the way out of the signal handler too, and
    # a handler that ran twice would decide twice -- the second time after the
    # first had already changed what it was looking at.
    assert stderr.count("deployment interrupted by SIG") == 1, stderr


@pytest.mark.parametrize("signal_name", ["SIGHUP", "SIGINT", "SIGTERM"])
def test_a_signalled_deployment_after_swap_restores_the_previous_release(
    deploy_harness: DeployHarness, signal_name: str
) -> None:
    """After `current` points at the candidate but before the active health
    check succeeds, a cancellation must roll back rather than leave the host
    serving a release that never finished activating."""
    import signal as signal_module

    number = getattr(signal_module, signal_name)
    previous_sha = "0" * 40
    deploy_harness.seed_active_release()
    sha = "a" * 37 + "bcd"
    deploy_harness.publish_archive(sha)

    result = signal_during_deploy(
        deploy_harness,
        sha,
        signal_name=signal_name,
    )

    assert result.returncode != 0, result.stderr
    assert result.returncode in (128 + number, -number), result.returncode
    assert not (deploy_harness.releases / sha).exists()
    assert not (deploy_harness.incoming_root / sha).exists()
    assert deploy_harness.current.resolve().name == previous_sha
    assert deploy_harness.service_running
    assert "systemctl:restart" in deploy_harness.events
    assert "previous release restored" in result.stderr
    assert "deployment interrupted by SIG" in result.stderr
    assert "active-health" not in deploy_harness.events


@pytest.mark.parametrize("signal_name", ["SIGHUP", "SIGINT", "SIGTERM"])
def test_a_signalled_first_deploy_after_swap_leaves_no_active_release(
    deploy_harness: DeployHarness, signal_name: str
) -> None:
    """With no previous release, a post-swap cancellation must remove `current`
    and stop the unit rather than leave a symlink to an unactivated tree."""
    import signal as signal_module

    number = getattr(signal_module, signal_name)
    sha = "b" * 37 + "cde"
    deploy_harness.publish_archive(sha)

    result = signal_during_deploy(
        deploy_harness,
        sha,
        signal_name=signal_name,
    )

    assert result.returncode != 0, result.stderr
    assert result.returncode in (128 + number, -number), result.returncode
    assert not deploy_harness.current.exists()
    assert not deploy_harness.service_running
    assert not (deploy_harness.releases / sha).exists()
    assert not (deploy_harness.incoming_root / sha).exists()
    assert "deployment interrupted by SIG" in result.stderr
    assert "systemctl:stop" in deploy_harness.events


@pytest.mark.parametrize("signal_name", ["SIGHUP", "SIGINT", "SIGTERM"])
def test_a_signalled_deployment_after_replace_before_flag_restores_the_previous_release(
    deploy_harness: DeployHarness, signal_name: str
) -> None:
    """`os.replace` can finish before bash sets `candidate_swapped`. Cleanup
    must resolve `current` against the candidate rather than trust the flag."""
    import signal as signal_module

    number = getattr(signal_module, signal_name)
    previous_sha = "0" * 40
    deploy_harness.seed_active_release()
    sha = "a" * 37 + "bcd"
    deploy_harness.publish_archive(sha)

    result = signal_during_deploy(
        deploy_harness,
        sha,
        signal_name=signal_name,
        pause_point="after_replace_before_flag",
    )

    assert result.returncode != 0, result.stderr
    assert result.returncode in (128 + number, -number), result.returncode
    assert deploy_harness.current.resolve().name == previous_sha
    assert not (deploy_harness.releases / sha).exists()
    assert not (deploy_harness.incoming_root / sha).exists()
    assert "previous release restored" in result.stderr
    assert "deployment interrupted by SIG" in result.stderr


def test_the_cleanup_handler_runs_once_even_when_a_signal_precedes_the_exit(
    deploy_harness: DeployHarness,
) -> None:
    """One handler on four traps is one handler that can run four times.

    The removals are idempotent, but the second pass runs after `current` may
    already have been restored, and a handler that re-entered while the first
    was still deciding is a handler whose decision depends on timing. So it
    disarms itself, and this is the test that says so.
    """
    installer = DEPLOYER.read_text(encoding="utf-8")

    assert re.search(r"^trap on_exit EXIT$", installer, re.MULTILINE)
    for name in ("HUP", "INT", "TERM"):
        assert re.search(rf"^trap 'on_signal {name}' {name}$", installer, re.MULTILINE)

    # Disarmed by both entry points, before either does any work, and the work
    # itself is behind a latch as well.
    assert len(re.findall(r"trap - EXIT HUP INT TERM", installer)) == 2
    assert "cleanup_done=0" in installer
    assert re.search(r"if \(\( cleanup_done == 1 \)\); then\s+return 0", installer)


def test_cleanup_rollback_resolves_current_against_the_candidate() -> None:
    """The swap latch is set before `os.replace` can finish, and rollback still
    resolves `current` against the candidate so already-active redeploys do not
    roll back."""
    installer = DEPLOYER.read_text(encoding="utf-8")

    assert "current_points_at_candidate()" in installer
    assert re.search(
        r"if current_points_at_candidate && \(\( candidate_swapped == 1 \)\)",
        installer,
    )
    swap_index = installer.index('swap_current "$candidate"')
    swapped_index = installer.rindex("candidate_swapped=1", 0, swap_index)
    assert swapped_index < swap_index
    assert "maybe_test_pause after_replace_before_flag" in installer


def test_a_relative_current_symlink_is_never_mistaken_for_a_stale_candidate(
    deploy_harness: DeployHarness,
) -> None:
    """`readlink` returns the link's target as written.

    `swap_current` writes an absolute one, but `current` is a file on a host
    that outlives any one version of this script -- an older deploy, a manual
    repair, or a restore could leave `current -> releases/<sha>`. Compared as
    text against an absolute candidate path that never matches, so the cleanup
    would have deleted the tree the host was serving on the next failed
    redeploy of that very SHA.
    """
    sha = "b" * 37 + "cde"
    live = deploy_harness.make_release(sha)
    deploy_harness.current.unlink(missing_ok=True)
    # Relative, and pointing at the same directory.
    deploy_harness.current.symlink_to(Path("releases") / sha)

    assert deploy_harness.current.resolve() == live.resolve()
    assert not os.path.isabs(os.readlink(deploy_harness.current))

    # A redeploy of the live SHA that fails after the tree exists. The early
    # "already active" exit must recognise it, and if anything downstream ever
    # stops recognising it, the cleanup must still refuse to delete it.
    deploy_harness.publish_archive(sha)
    result = deploy_harness.run(sha)

    assert result.returncode == 0
    assert "already active" in result.stderr
    assert (live / "site" / "index.html").is_file()
    assert deploy_harness.current.resolve() == live.resolve()


def test_a_relative_current_symlink_survives_a_failing_deployment(
    deploy_harness: DeployHarness,
) -> None:
    """The same relative link, and a different SHA whose deployment fails.

    The rolled-back link has to still resolve, and the release it names has to
    still be there for the next deploy to fall back to.
    """
    live_sha = "c" * 37 + "def"
    live = deploy_harness.make_release(live_sha)
    (live / "MARKER").write_text("live\n", encoding="utf-8")
    deploy_harness.current.unlink(missing_ok=True)
    deploy_harness.current.symlink_to(Path("releases") / live_sha)

    failing = "d" * 37 + "def"
    deploy_harness.publish_unextractable_archive(failing)

    assert deploy_harness.run(failing).returncode != 0
    assert (live / "MARKER").is_file()
    assert deploy_harness.current.resolve() == live.resolve()
    assert not (deploy_harness.releases / failing).exists()


def test_redeploying_the_active_release_keeps_the_tree_it_is_serving(
    deploy_harness: DeployHarness,
) -> None:
    """A same-SHA deployment reloads runtime configuration without replacing
    the directory systemd is already serving."""
    sha = "7" * 38 + "de"
    deploy_harness.publish_archive(sha)
    assert deploy_harness.run(sha).returncode == 0
    deploy_harness.events_file.unlink()

    deploy_harness.publish_archive(sha)
    result = deploy_harness.run(sha)

    assert result.returncode == 0
    assert "already active" in result.stderr
    assert deploy_harness.events.count("systemctl:restart") == 1
    assert "active-health" in deploy_harness.events
    assert deploy_harness.events.index("candidate-health") < deploy_harness.events.index(
        "systemctl:restart"
    )
    assert (deploy_harness.releases / sha / "site" / "index.html").is_file()
    assert deploy_harness.current.resolve().name == sha


def test_a_successful_deployment_keeps_the_tree_it_just_activated(
    deploy_harness: DeployHarness,
) -> None:
    """The guard is a status check, not a blanket removal: a zero exit after a
    healthy activation must leave the release the host is now serving."""
    deploy_harness.seed_active_release()
    sha = "8" * 38 + "de"
    deploy_harness.publish_archive(sha)

    assert deploy_harness.run(sha).returncode == 0
    assert (deploy_harness.releases / sha / ".venv").is_dir()
    assert deploy_harness.current.resolve().name == sha


def test_a_failed_prune_after_activation_still_keeps_the_live_release(
    deploy_harness: DeployHarness,
) -> None:
    """Pruning runs after activation and is allowed to fail, so the exit status
    is zero and the cleanup must not fire -- but the ordering is worth pinning
    directly, because a cleanup keyed on anything other than "did this become
    the live release" would delete the release that is serving traffic."""
    stale = [deploy_harness.make_release(digit * 40) for digit in "123"]
    for offset, release in enumerate(stale, start=20):
        deploy_harness.set_mtime(release, offset)
    unprunable = stale[0]
    unprunable.chmod(0o555)

    sha = "9" * 38 + "de"
    deploy_harness.publish_archive(sha)

    try:
        result = deploy_harness.run(sha)
    finally:
        unprunable.chmod(0o755)

    assert result.returncode == 0, result.stderr
    assert (deploy_harness.releases / sha / "site" / "index.html").is_file()
    assert deploy_harness.current.resolve().name == sha


# --- Configuration reaches the service without ever being evaluated ----------

# The same values `server/tests/test_ec2_infra.py` renders through the
# bootstrap, on the other side of the channel. There they have to survive
# being written; here they have to survive being loaded.
HOSTILE_CONFIGURATION_VALUES = (
    "a value with spaces",
    "/opt/authifi docs/current/site",
    "$(touch CANARY)",
    "${CANARY}",
    "`touch CANARY`",
    "x; touch CANARY",
    "x && touch CANARY",
    'a "quoted" value',
    "a 'quoted' value",
    "a\\backslash",
    "a#hash",
    "a$dollar",
    "a=equals=sign",
    "https://issuer.example/authorize?a=b&c=d#frag",
)


def test_the_configured_environment_reaches_the_candidate_server(
    deploy_harness: DeployHarness,
) -> None:
    sha = "a" * 38 + "de"
    deploy_harness.publish_archive(sha)

    assert deploy_harness.run(sha).returncode == 0

    environment = deploy_harness.candidate_environment

    for name, value in HOST_CONFIGURATION.items():
        if name != "SITE_DIR":
            assert environment[name] == value
    # From `session.env`, which is generated on the host and never in Terraform.
    assert environment["SESSION_SECRET"] == "0123456789abcdef"
    # And the probe still overrides the site the candidate serves, because it
    # has to serve the candidate's own tree rather than the active one's.
    assert environment["SITE_DIR"] == str(deploy_harness.releases / sha / "site")


@pytest.mark.parametrize("value", HOSTILE_CONFIGURATION_VALUES)
def test_a_shell_significant_configured_value_is_loaded_and_never_run(
    deploy_harness: DeployHarness, value: str
) -> None:
    """This installer runs as root under the Systems Manager agent, and it used
    to `source` the file these values arrive in.

    An accepted absolute `site_dir` containing a space split into an assignment
    plus a command and aborted every deployment; a command substitution or a
    semicolon in any value ran as root. The values are parsed now and exported
    one word at a time, so nothing re-evaluates a right-hand side -- which is
    what lets the legitimate punctuation in the last case through as well.
    """
    canary = deploy_harness.tmp_path / "CANARY"
    expected = value.replace("CANARY", str(canary))
    deploy_harness.write_configuration(
        {**HOST_CONFIGURATION, "OIDC_ISSUER": expected, "POST_LOGOUT_PATH": expected}
    )
    sha = "b" * 38 + "de"
    deploy_harness.publish_archive(sha)

    result = deploy_harness.run(sha)

    assert result.returncode == 0, result.stderr
    assert deploy_harness.candidate_environment["OIDC_ISSUER"] == expected
    assert deploy_harness.candidate_environment["POST_LOGOUT_PATH"] == expected
    assert not canary.exists()


def malformed_configurations() -> list[tuple[str, str]]:
    """Every configuration document the installer must refuse, as raw text.

    Raw rather than built from a dict, because two of these -- a duplicated key
    and a key that is not a string -- cannot be expressed as one.
    """
    good = dict(HOST_CONFIGURATION)
    cases = [
        ("not-json", "not json at all"),
        ("json-array", '["a list", "not an object"]'),
        ("json-string", '"a bare string"'),
        ("empty-object", "{}"),
    ]

    # A missing key would start the service with the previous release's value
    # for it, or with none at all, rather than with what Terraform set.
    for name in good:
        cases.append(
            (f"missing-{name}", json.dumps({k: v for k, v in good.items() if k != name}))
        )

    # An extra key is the injection channel. `PATH` and `LD_PRELOAD` are the
    # two that decide what root runs and what it loads before dropping
    # privileges, and they are refused by the same rule that refuses a typo.
    for extra in ("PATH", "LD_PRELOAD", "LD_LIBRARY_PATH", "BASH_ENV", "PYTHONPATH",
                  "OIDC_CLIENT_SECRET", "SESSION_SECRET", "OIDC_ISSUE"):
        cases.append((f"extra-{extra}", json.dumps({**good, extra: "/attacker"})))

    # JSON permits a repeated name and Python keeps the last silently, so a
    # document whose first `SITE_DIR` an operator reads is not the one that
    # would take effect.
    body = ", ".join(f'"{name}": "{value}"' for name, value in good.items())
    cases.append(("duplicate-key", "{" + body + ', "SITE_DIR": "/attacker"}'))

    for name, bad in (
        ("lowercase-key", '{"site_dir": "/x"}'),
        ("spaced-key", '{"SITE DIR": "/x"}'),
        ("non-string-value", '{"SITE_DIR": 8080}'),
        ("null-value", '{"SITE_DIR": null}'),
        ("list-value", '{"SITE_DIR": ["/x"]}'),
        ("newline-value", '{"SITE_DIR": "carries\\na newline"}'),
        ("nul-value", '{"SITE_DIR": "carries\\u0000a nul"}'),
    ):
        document = json.loads(bad)
        cases.append((name, bad[:-1] + ", " + ", ".join(
            f'"{k}": "{v}"' for k, v in good.items() if k not in document
        ) + "}"))

    return cases


@pytest.mark.parametrize(
    "configuration",
    [pytest.param(body, id=name) for name, body in malformed_configurations()],
)
def test_configuration_the_installer_cannot_trust_stops_the_deployment(
    deploy_harness: DeployHarness, configuration: str
) -> None:
    """A parser that skipped what it did not understand, or accepted more than
    it needed, would be a parser that starts the service with a silently
    different environment -- or one an extra key can steer."""
    old = deploy_harness.seed_active_release()
    (deploy_harness.etc / "config.json").write_text(configuration, encoding="utf-8")
    sha = "c" * 37 + "ade"
    deploy_harness.publish_archive(sha)

    result = deploy_harness.run(sha)

    assert result.returncode != 0
    assert deploy_harness.current.resolve() == old
    assert deploy_harness.candidate_environment == {}
    assert not (deploy_harness.releases / sha).exists()


@pytest.mark.parametrize("name", ["PATH", "LD_PRELOAD", "BASH_ENV"])
def test_a_configuration_key_cannot_steer_what_root_runs(
    deploy_harness: DeployHarness, name: str
) -> None:
    """`env NAME=VALUE cmd` resolves `cmd` under the environment it just set,
    so a `PATH` in the configuration would choose which `timeout` root
    executes, and an `LD_PRELOAD` would choose what it loads -- both before
    `setpriv` drops to the service account.

    Two things have to be true for that to be closed, and this asserts both:
    the key set refuses the name, and the binaries root execs are absolute
    paths that no environment variable can redirect.
    """
    canary = deploy_harness.tmp_path / "CANARY"
    hostile = deploy_harness.tmp_path / "hostile-bin"
    hostile.mkdir()
    for command in ("timeout", "setpriv", "env", "python3"):
        shim = hostile / command
        shim.write_text(
            f"#!/bin/sh\ntouch {canary}\nexit 1\n",
            encoding="utf-8",
        )
        shim.chmod(0o755)

    deploy_harness.write_configuration({**HOST_CONFIGURATION, name: str(hostile)})
    sha = "d" * 37 + "ade"
    deploy_harness.publish_archive(sha)

    result = deploy_harness.run(sha)

    assert result.returncode != 0
    assert not canary.exists(), f"{name} in the configuration reached a root exec"


def test_the_installer_execs_binaries_no_environment_variable_can_redirect() -> None:
    """The defaults are absolute paths, so `env` is handed a command it cannot
    resolve differently no matter what it was told to set.

    Overridable, because the tests point them at fakes, but the value a
    production host uses is the one written here.
    """
    installer = DEPLOYER.read_text(encoding="utf-8")
    defaults = dict(
        re.findall(r'^(\w+)="\$\{AUTHIFI_DOCS_\w+:-([^}]*)\}"', installer, re.MULTILINE)
    )

    for name in ("python_bin", "curl_bin", "systemctl_bin", "timeout_bin",
                 "setpriv_bin", "env_bin"):
        assert defaults.get(name, "").startswith("/"), f"{name} default is not absolute"

    # And the `env` that applies them is the resolved one, not a bare word.
    assert re.search(r'^\s*"\$env_bin" \$\{service_environment', installer, re.MULTILINE)


def test_the_installer_requires_the_session_secret_and_nothing_else(
    deploy_harness: DeployHarness,
) -> None:
    """`session.env` is generated on the host, but it is read with the same
    suspicion: it is a file on the deployment path, and the bootstrap writes
    exactly one assignment into it."""
    (deploy_harness.etc / "session.env").write_text(
        "SESSION_SECRET=abc\nPATH=/attacker\n", encoding="utf-8"
    )
    sha = "e" * 37 + "ade"
    deploy_harness.publish_archive(sha)

    result = deploy_harness.run(sha)

    assert result.returncode != 0
    assert deploy_harness.candidate_environment == {}


def test_the_installer_hands_over_the_configuration_in_one_fixed_order(
    deploy_harness: DeployHarness,
) -> None:
    """Two runs of the same configuration produce the same command line.

    Not cosmetic: the assignments are the argument list of a root `env`, and an
    order that depended on JSON member order or on dict iteration would make
    the one thing an operator can read in an SSM log differ between runs of the
    same release for no reason.
    """
    shuffled = dict(reversed(list(HOST_CONFIGURATION.items())))

    assert list(shuffled) != list(HOST_CONFIGURATION)

    orders = []
    for index, configuration in enumerate((HOST_CONFIGURATION, shuffled)):
        deploy_harness.write_configuration(configuration)
        sha = f"{index}" * 37 + "ade"
        deploy_harness.publish_archive(sha)

        assert deploy_harness.run(sha).returncode == 0

        orders.append(deploy_harness.candidate_environment_order)

    assert orders[0] == orders[1]
    # `SITE_DIR` last on purpose, so the candidate serves its own tree; `env`
    # applies the later assignment.
    assert orders[0][-1] == "SITE_DIR"
    assert orders[0][:-1] == sorted(orders[0][:-1])


def test_the_candidate_server_is_probed_as_the_service_account(
    deploy_harness: DeployHarness,
) -> None:
    """The candidate probe answers "will the release systemd is about to start
    actually serve?", and root can read a site the service user cannot. Probed
    as root, that difference surfaces after the swap instead of before it.
    """
    deploy_harness.seed_active_release()
    sha = "9" * 38 + "cd"
    deploy_harness.publish_archive(sha)

    assert deploy_harness.run(sha).returncode == 0

    invocations = deploy_harness.setpriv_invocations
    assert len(invocations) == 1

    arguments = invocations[0]
    separator = arguments.index("--")

    assert arguments[:separator] == [
        "--reuid=authifi-docs",
        "--regid=authifi-docs",
        "--init-groups",
        "--no-new-privs",
    ]

    command = arguments[separator + 1 :]
    assert command[0] == str(deploy_harness.fake_bin / "uvicorn")
    assert "server.main:app" in command
    assert command[-2:] == ["--port", "18080"]


def test_an_occupied_candidate_port_fails_before_the_swap(
    deploy_harness: DeployHarness,
) -> None:
    """A leftover uvicorn from an interrupted deploy still holding the candidate
    port would answer the health check, and the release that passed would be the
    old one: a candidate promoted without ever having been probed.

    The port is one the OS hands out rather than 18080, so this test cannot
    collide with whatever is listening on the machine running it.
    """
    old = deploy_harness.seed_active_release()
    sha = "a" * 38 + "cd"
    deploy_harness.publish_archive(sha)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
        holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        deploy_harness.candidate_port = holder.getsockname()[1]

        result = deploy_harness.run(sha)

    assert result.returncode != 0
    assert "already in use" in result.stderr
    assert deploy_harness.current.resolve() == old
    assert deploy_harness.candidate_pid is None
    assert deploy_harness.setpriv_invocations == []
    assert "systemctl:restart" not in deploy_harness.events
    assert not (deploy_harness.incoming_root / sha).exists()


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


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the mode bits this relies on")
def test_a_failed_prune_does_not_fail_an_activated_deployment(
    deploy_harness: DeployHarness,
) -> None:
    """Pruning runs after the release is live, and it is only housekeeping.

    `prune_releases` was the installer's last statement, so under `set -e`
    anything that went wrong inside it became the script's exit status: an
    `EPERM` on one stale directory, an `EBUSY` from something still holding a
    file open, an `ENOSPC` part-way through an rmtree. Systems Manager reports
    that as a failed command and the workflow treats it as a failed
    deployment -- on a host that is serving the new release and passing its
    health check. The operator is then told to roll back a deployment that
    worked, because a directory nobody is going to read again could not be
    deleted.

    The failure is forced here only where it can be reached: after the swap,
    the restart, and the active health check have all succeeded.
    """
    stale = [deploy_harness.make_release(digit * 40) for digit in "123"]
    for offset, release in enumerate(stale, start=20):
        deploy_harness.set_mtime(release, offset)

    # The oldest, so it is the one release `prune_releases` reaches -- two
    # non-active releases are kept. Clearing write on the release directory is
    # what makes rmtree fail: it can list and empty `site/`, then cannot rmdir
    # it. A real `chmod`, not a stubbed error, so the installer is answering
    # the same failure the kernel would give it on the host.
    unprunable = stale[0]
    unprunable.chmod(0o555)

    sha = "d" * 40
    deploy_harness.publish_archive(sha)

    try:
        result = deploy_harness.run(sha)
    finally:
        # Restored unconditionally: a directory left unwritable here would
        # break pytest's own cleanup of this tmp_path on a later run.
        unprunable.chmod(0o755)

    # Activated and verified, all before pruning was reachable at all.
    assert deploy_harness.current.resolve().name == sha
    assert deploy_harness.service_running
    assert "active-health" in deploy_harness.events

    assert result.returncode == 0, result.stderr
    assert "release pruning failed; deployment is active" in result.stderr

    # Nothing rolled back and nothing restarted twice: this is a successful
    # deployment that could not tidy up, not a failure that recovered.
    assert "previous release" not in result.stderr
    assert deploy_harness.events.count("systemctl:restart") == 1
    assert unprunable.is_dir()


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
