from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
import re
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
RAW_WORKFLOW = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(
    encoding="utf-8"
)
WORKFLOW = yaml.safe_load(RAW_WORKFLOW)
DEPLOY_JOB = WORKFLOW["jobs"]["deploy"]
STEPS = DEPLOY_JOB["steps"]


def parse_steps(text: str) -> list[dict[str, object]]:
    return yaml.safe_load(text)["jobs"]["deploy"]["steps"]


def step(name: str) -> dict[str, object]:
    return next(candidate for candidate in STEPS if candidate.get("name") == name)


def step_index(name: str) -> int:
    return next(index for index, candidate in enumerate(STEPS) if candidate.get("name") == name)


def step_if(name: str) -> str | None:
    value = step(name).get("if")
    return None if value is None else str(value)


def step_uses(name: str) -> str | None:
    value = step(name).get("uses")
    return None if value is None else str(value)


def step_run(name: str) -> str:
    return str(step(name).get("run", ""))


def executable_lines(run: str) -> list[str]:
    lines: list[str] = []
    pending = ""

    for raw_line in run.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        pending = f"{pending} {stripped}".strip() if pending else stripped
        if pending.endswith("\\"):
            pending = pending[:-1].rstrip()
            continue

        lines.append(pending)
        pending = ""

    if pending:
        lines.append(pending)
    return lines


def anchored_lines(lines: list[str], pattern: str) -> list[str]:
    regex = re.compile(pattern)
    return [line for line in lines if regex.match(line)]


def anchored_line(run: str, pattern: str) -> str:
    matches = anchored_lines(executable_lines(run), pattern)
    assert len(matches) == 1, matches
    return matches[0]


def assert_failure_branch(lines: list[str], command_line: str, diagnostic_line: str) -> None:
    index = lines.index(command_line)
    assert lines[index + 1] == diagnostic_line
    assert lines[index + 2] == "exit 1"
    assert lines[index + 3] == "fi"


def assert_rollback_head_object_guards(run: str) -> None:
    lines = executable_lines(run)
    head_object_lines = anchored_lines(lines, r"^if ! aws s3api head-object\b")

    assert head_object_lines == [
        'if ! aws s3api head-object --bucket "$RELEASE_BUCKET_NAME" --key "$archive_key" > /dev/null 2>&1; then',
        'if ! aws s3api head-object --bucket "$RELEASE_BUCKET_NAME" --key "$checksum_key" > /dev/null 2>&1; then',
    ]
    for line in head_object_lines:
        assert '--bucket "$RELEASE_BUCKET_NAME"' in line

    assert_failure_branch(
        lines,
        head_object_lines[0],
        'echo "Existing release archive not found in s3://$RELEASE_BUCKET_NAME/$archive_key" >&2',
    )
    assert_failure_branch(
        lines,
        head_object_lines[1],
        'echo "Existing release checksum not found in s3://$RELEASE_BUCKET_NAME/$checksum_key" >&2',
    )


def with_line_replaced(run: str, original: str, replacement: str) -> str:
    assert original in run, original
    return run.replace(original, replacement, 1)


def test_deploy_job_uses_pinned_oidc_and_expected_step_shape() -> None:
    assert WORKFLOW["permissions"] == {"contents": "read", "id-token": "write"}
    assert DEPLOY_JOB["environment"] == "production"

    assert step_uses("Checkout repo") == (
        "actions/checkout@11d5960a326750d5838078e36cf38b85af677262"
    )
    assert step_uses("Set up Python") == (
        "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065"
    )
    assert step_uses("Configure AWS credentials") == (
        "aws-actions/configure-aws-credentials@7474bc4690e29a8392af63c5b98e7449536d5c3a"
    )

    assert [candidate["name"] for candidate in STEPS] == [
        "Checkout repo",
        "Set up Python",
        "Install Python dependencies",
        "Verify required repository variables",
        "Configure AWS credentials",
        "Synchronize OIDC client secret",
        "Select release",
        "Build release",
        "Publish or verify release",
        "Verify existing release for rollback",
        "Install release through SSM",
        "Wait for installer",
        "Wait for healthy ALB target",
        "Verify public and protected routes",
    ]


def test_required_repository_variables_are_validated_before_aws_mutation() -> None:
    verify_name = "Verify required repository variables"
    configure_name = "Configure AWS credentials"

    assert step_index(verify_name) < step_index(configure_name)
    assert step_if(verify_name) is None

    run = step_run(verify_name)
    for variable in (
        "AWS_REGION",
        "AWS_DEPLOY_ROLE_ARN",
        "RELEASE_BUCKET_NAME",
        "DOCS_INSTANCE_ID",
        "DOCS_SSM_DOCUMENT_NAME",
        "DOCS_TARGET_GROUP_ARN",
        "DOCS_ALB_DNS_NAME",
        "DOCS_PUBLIC_BASE_URL",
    ):
        assert f': "${{{variable}:?Set repository variable {variable}}}"' in run


def test_environment_secret_is_synchronized_before_the_ssm_deployment() -> None:
    sync_name = "Synchronize OIDC client secret"
    sync = step(sync_name)
    run = step_run(sync_name)

    assert step_index("Configure AWS credentials") < step_index(sync_name)
    assert step_index(sync_name) < step_index("Install release through SSM")
    assert sync["env"] == {
        "OIDC_CLIENT_SECRET": "${{ secrets.OIDC_CLIENT_SECRET }}"
    }
    assert ': "${OIDC_CLIENT_SECRET:?Set production environment secret OIDC_CLIENT_SECRET}"' in run
    assert 'aws ssm put-parameter \\' in run
    assert '--name "/authifi-docs/oidc-client-secret" \\' in run
    assert "--type SecureString \\" in run
    assert '--value "$OIDC_CLIENT_SECRET" \\' in run
    assert "--overwrite >/dev/null" in run
    assert "set -x" not in run


def test_select_release_handles_push_and_workflow_dispatch_safely() -> None:
    selected = step("Select release")

    assert selected["id"] == "release"
    assert selected["shell"] == "bash"
    assert selected["env"]["REQUESTED_RELEASE_SHA"] == (
        "${{ github.event_name == 'workflow_dispatch' && inputs.release_sha || '' }}"
    )

    run = step_run("Select release")
    lines = executable_lines(run)
    assert 'if [[ -n "$requested_sha" ]]; then' in lines
    assert 'sha="$requested_sha"' in lines
    assert "build=false" in lines
    assert 'sha="$GITHUB_SHA"' in lines
    assert "build=true" in lines
    assert 'echo "Release SHA must be 40 lowercase hexadecimal characters" >&2' in lines


def test_push_and_rerun_build_steps_are_gated_on_having_built() -> None:
    assert step_if("Build release") == "${{ steps.release.outputs.build == 'true' }}"
    assert step_if("Publish or verify release") == "${{ steps.release.outputs.build == 'true' }}"

    build_run = step_run("Build release")

    assert 'set -euo pipefail' in executable_lines(build_run)
    assert (
        anchored_line(
            build_run,
            r'^\.\/scripts\/build-release\.sh "\$\{\{ steps\.release\.outputs\.sha \}\}" dist/releases$',
        )
        == './scripts/build-release.sh "${{ steps.release.outputs.sha }}" dist/releases'
    )


# --- Publishing a release into S3 ---------------------------------------------
#
# The step is read structurally *and* executed. The structural half pins the
# commands and their order; the executable half is what says which branch runs
# against which bucket state, and it is the only half a rewritten condition
# cannot satisfy by accident.

PUBLISH_STEP = "Publish or verify release"

# The release the harness publishes. Any 40-hex value; spelled out so the
# assertions can name the keys.
HARNESS_SHA = "b" * 40
HARNESS_BUCKET = "authifi-docs-releases-123456789012"

FAKE_AWS = """#!/usr/bin/env python3
\"\"\"Just enough of the S3 API for the publish step, and no more.

`head-object` answers from a directory of objects, `s3 cp` moves bytes in and
out of it, and `put-object` honours `--if-none-match` the way S3 documents:
412 if a current version already exists. Every invocation is recorded, so a
test can assert not only what happened but that nothing else did.
\"\"\"

import json
import os
import shutil
import sys
from pathlib import Path

root = Path(os.environ["FAKE_S3_ROOT"])
calls = Path(os.environ["FAKE_S3_CALLS"])
arguments = sys.argv[1:]

with calls.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(arguments) + "\\n")


def fail(message):
    print(message, file=sys.stderr)
    sys.exit(254)


def flags(tokens):
    parsed = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.startswith("--"):
            value = ""
            if index + 1 < len(tokens) and not tokens[index + 1].startswith("--"):
                value = tokens[index + 1]
                index += 1
            parsed[token] = value
        index += 1
    return parsed


def object_path(bucket, key):
    return root / bucket / key


def appearing_keys():
    \"\"\"Keys a concurrent publisher creates just before this write lands.\"\"\"
    schedule = os.environ.get("FAKE_S3_APPEAR_ON_PUT", "")
    return [entry for entry in schedule.split(",") if entry]


if arguments[:2] == ["s3api", "head-object"]:
    parsed = flags(arguments[2:])
    target = object_path(parsed["--bucket"], parsed["--key"])
    if not target.is_file():
        fail("An error occurred (404) when calling the HeadObject operation: Not Found")
    print(json.dumps({"ContentLength": target.stat().st_size}))
    sys.exit(0)

if arguments[:2] == ["s3api", "put-object"]:
    parsed = flags(arguments[2:])
    target = object_path(parsed["--bucket"], parsed["--key"])
    for key in appearing_keys():
        raced = object_path(parsed["--bucket"], key)
        raced.parent.mkdir(parents=True, exist_ok=True)
        if not raced.is_file():
            raced.write_bytes(b"published by somebody else")
    if parsed.get("--if-none-match") == "*" and target.is_file():
        fail(
            "An error occurred (PreconditionFailed) when calling the PutObject "
            "operation: At least one of the pre-conditions you specified did not hold"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(parsed["--body"], target)
    print(json.dumps({"ETag": '"0"'}))
    sys.exit(0)

if arguments[:2] == ["s3", "cp"]:
    source, destination = arguments[2], arguments[3]
    if source.startswith("s3://") and not destination.startswith("s3://"):
        bucket, _, key = source.removeprefix("s3://").partition("/")
        origin = object_path(bucket, key)
        if not origin.is_file():
            fail(f"fatal error: An error occurred (404) when calling HeadObject: Key {key}")
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, destination)
        sys.exit(0)
    if destination.startswith("s3://") and not source.startswith("s3://"):
        # Deliberately unimplemented. An upload has to go through put-object,
        # because that is the only call that can refuse to overwrite.
        fail("fatal error: this harness refuses unconditional uploads")
    fail(f"fatal error: unsupported copy {source} -> {destination}")

fail(f"fatal error: unsupported invocation {arguments}")
"""

PYTHON_SHIM = f"""#!/usr/bin/env python3
import os
import sys

os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])
"""


@dataclass
class PublishHarness:
    """The publish step, run for real against a directory pretending to be S3."""

    tmp_path: Path
    bucket: Path = field(init=False)
    calls_file: Path = field(init=False)
    script: Path = field(init=False)
    env: dict[str, str] = field(init=False)

    def __post_init__(self) -> None:
        s3_root = self.tmp_path / "s3"
        self.bucket = s3_root / HARNESS_BUCKET
        self.bucket.mkdir(parents=True)
        self.calls_file = self.tmp_path / "aws-calls.jsonl"
        fake_bin = self.tmp_path / "fake-bin"
        fake_bin.mkdir()
        self._write_executable(fake_bin / "aws", FAKE_AWS)
        self._write_executable(fake_bin / "python", PYTHON_SHIM)

        self.script = self.tmp_path / "publish-step.sh"
        self.script.write_text(step_run(PUBLISH_STEP), encoding="utf-8")

        (self.tmp_path / "dist" / "releases").mkdir(parents=True)
        self.env = {
            **os.environ,
            "RELEASE_BUCKET_NAME": HARNESS_BUCKET,
            "RELEASE_SHA": HARNESS_SHA,
            "FAKE_S3_ROOT": str(s3_root),
            "FAKE_S3_CALLS": str(self.calls_file),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
        }

    @staticmethod
    def _write_executable(path: Path, body: str) -> None:
        path.write_text(body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def build(self, content: bytes = b"the release this run built") -> None:
        """Write the archive and checksum `scripts/build-release.sh` would."""
        archive = self.tmp_path / "dist" / "releases" / f"{HARNESS_SHA}.tar.gz"
        archive.write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        archive.with_suffix(".gz.sha256").write_text(
            f"{digest}  {HARNESS_SHA}.tar.gz\n", encoding="utf-8"
        )

    def publish_archive(self, content: bytes) -> None:
        self._put(f"releases/{HARNESS_SHA}.tar.gz", content)

    def publish_checksum_for(self, content: bytes) -> None:
        digest = hashlib.sha256(content).hexdigest()
        self._put(
            f"releases/{HARNESS_SHA}.tar.gz.sha256",
            f"{digest}  {HARNESS_SHA}.tar.gz\n".encode("utf-8"),
        )

    def publish_raw_checksum(self, body: str) -> None:
        self._put(f"releases/{HARNESS_SHA}.tar.gz.sha256", body.encode("utf-8"))

    def _put(self, key: str, body: bytes) -> None:
        target = self.bucket / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)

    def race_on_put(self, *keys: str) -> None:
        """Let another publisher win each key between the check and the write."""
        self.env["FAKE_S3_APPEAR_ON_PUT"] = ",".join(keys)

    def run(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(self.script)],
            cwd=self.tmp_path,
            capture_output=True,
            text=True,
            env=self.env,
        )

    @property
    def calls(self) -> list[list[str]]:
        if not self.calls_file.exists():
            return []
        return [
            json.loads(line)
            for line in self.calls_file.read_text(encoding="utf-8").splitlines()
        ]

    @property
    def written_keys(self) -> list[str]:
        """Every key this run uploaded, in the order it uploaded them."""
        return [
            call[call.index("--key") + 1]
            for call in self.calls
            if call[:2] == ["s3api", "put-object"]
        ]

    def published(self, suffix: str) -> bytes | None:
        target = self.bucket / f"releases/{HARNESS_SHA}.tar.gz{suffix}"
        return target.read_bytes() if target.is_file() else None


@pytest.fixture
def publish_harness(tmp_path: Path) -> PublishHarness:
    return PublishHarness(tmp_path)


def test_the_publish_step_is_the_script_the_harness_runs() -> None:
    """The harness executes `run:` verbatim, so an unexpanded `${{ }}` in it
    would be a syntax error at best and a silently empty value at worst.

    Every input therefore arrives through `env:`, where the expansion happens
    outside the shell -- which is also what keeps a workflow expression from
    being spliced into the script text.
    """
    step_definition = step(PUBLISH_STEP)
    run = step_run(PUBLISH_STEP)

    assert "${{" not in run
    assert step_definition["env"] == {"RELEASE_SHA": "${{ steps.release.outputs.sha }}"}
    for variable in ("RELEASE_SHA", "RELEASE_BUCKET_NAME"):
        assert f"${variable}" in run


def test_a_first_publication_uploads_the_archive_before_its_checksum(
    publish_harness: PublishHarness,
) -> None:
    """The order matters on the way out as well as on the way in: a run
    interrupted after the archive leaves a state the next run can complete,
    and the installer refuses a release whose checksum is missing rather than
    installing unverified bytes."""
    publish_harness.build()

    result = publish_harness.run()

    assert result.returncode == 0, result.stderr
    assert publish_harness.written_keys == [
        f"releases/{HARNESS_SHA}.tar.gz",
        f"releases/{HARNESS_SHA}.tar.gz.sha256",
    ]


def test_a_fully_published_release_is_verified_and_left_alone(
    publish_harness: PublishHarness,
) -> None:
    content = b"the release this run built"
    publish_harness.build(content)
    publish_harness.publish_archive(content)
    publish_harness.publish_checksum_for(content)

    result = publish_harness.run()

    assert result.returncode == 0, result.stderr
    assert publish_harness.written_keys == []


def test_a_published_release_whose_checksum_disagrees_fails_without_writing(
    publish_harness: PublishHarness,
) -> None:
    publish_harness.build(b"the release this run built")
    publish_harness.publish_archive(b"the release this run built")
    publish_harness.publish_checksum_for(b"something else entirely")

    result = publish_harness.run()

    assert result.returncode != 0
    assert publish_harness.written_keys == []
    assert "checksum" in result.stderr.lower()


def test_an_interrupted_publication_is_completed_by_uploading_the_checksum(
    publish_harness: PublishHarness,
) -> None:
    """The state a cancelled run leaves behind, and the reason this step exists.

    `aws s3 cp` of the archive succeeded and the checksum upload never ran, so
    rerunning the same commit found the archive present and the checksum
    absent and failed on the spot. The release could then never be deployed
    through this workflow without somebody repairing the bucket by hand.
    """
    content = b"the release this run built"
    publish_harness.build(content)
    publish_harness.publish_archive(content)

    result = publish_harness.run()

    assert result.returncode == 0, result.stderr
    assert publish_harness.written_keys == [f"releases/{HARNESS_SHA}.tar.gz.sha256"]
    assert publish_harness.published("") == content
    assert publish_harness.published(".sha256") is not None
    assert hashlib.sha256(content).hexdigest() in publish_harness.published(".sha256").decode()


def test_an_interrupted_publication_of_different_bytes_is_refused(
    publish_harness: PublishHarness,
) -> None:
    """Recovery is only recovery if the published archive is this build.

    A checksum uploaded over an archive somebody else published would make
    that archive verifiable under this commit's SHA, which is exactly the
    substitution the checksum exists to prevent.
    """
    publish_harness.build(b"the release this run built")
    publish_harness.publish_archive(b"an archive from somewhere else")

    result = publish_harness.run()

    assert result.returncode != 0
    assert publish_harness.written_keys == []
    assert publish_harness.published(".sha256") is None
    assert publish_harness.published("") == b"an archive from somewhere else"


def test_a_checksum_published_without_its_archive_is_completed(
    publish_harness: PublishHarness,
) -> None:
    """The mirror-image partial state. Reachable from a hand-repaired bucket or
    a lifecycle expiry that reached one object first, and safe to complete for
    the same reason: the published checksum is the authority, and it matches
    the bytes this run built."""
    content = b"the release this run built"
    publish_harness.build(content)
    publish_harness.publish_checksum_for(content)

    result = publish_harness.run()

    assert result.returncode == 0, result.stderr
    assert publish_harness.written_keys == [f"releases/{HARNESS_SHA}.tar.gz"]
    assert publish_harness.published("") == content


def test_a_checksum_published_for_other_bytes_is_refused(
    publish_harness: PublishHarness,
) -> None:
    publish_harness.build(b"the release this run built")
    publish_harness.publish_checksum_for(b"an archive from somewhere else")

    result = publish_harness.run()

    assert result.returncode != 0
    assert publish_harness.written_keys == []
    assert publish_harness.published("") is None


@pytest.mark.parametrize(
    "body",
    [
        "",
        "not a checksum at all\n",
        f"deadbeef  {HARNESS_SHA}.tar.gz\n",
        "0000000000000000000000000000000000000000000000000000000000000000  other.tar.gz\n",
    ],
)
def test_a_malformed_published_checksum_is_refused_rather_than_compared(
    publish_harness: PublishHarness, body: str
) -> None:
    """An unparseable checksum file compared as a string would be an empty
    value on both sides of a comparison that then succeeds."""
    content = b"the release this run built"
    publish_harness.build(content)
    publish_harness.publish_archive(content)
    publish_harness.publish_raw_checksum(body)

    result = publish_harness.run()

    assert result.returncode != 0
    assert publish_harness.written_keys == []


def test_a_build_whose_own_checksum_disagrees_never_reaches_the_bucket(
    publish_harness: PublishHarness,
) -> None:
    """Everything below compares against the local checksum file, so it is
    checked against the local archive first."""
    publish_harness.build(b"the release this run built")
    archive = publish_harness.tmp_path / "dist" / "releases" / f"{HARNESS_SHA}.tar.gz"
    archive.write_bytes(b"rewritten after the checksum was taken")

    result = publish_harness.run()

    assert result.returncode != 0
    assert publish_harness.written_keys == []
    assert publish_harness.calls == []


@pytest.mark.parametrize("suffix", ["", ".sha256"])
def test_every_upload_refuses_to_overwrite_an_object_that_appeared_meanwhile(
    publish_harness: PublishHarness, suffix: str
) -> None:
    """The presence checks and the uploads cannot be one atomic step, so the
    upload carries the condition instead.

    `--if-none-match "*"` makes S3 answer 412 when a current version already
    exists, which turns the window between the check and the write from a
    silent overwrite into a failed deployment. The workflow's concurrency group
    already serialises runs; this is what holds when something outside it --
    a manual repair, another account -- writes the same key.
    """
    content = b"the release this run built"
    publish_harness.build(content)
    publish_harness.race_on_put(f"releases/{HARNESS_SHA}.tar.gz{suffix}")

    result = publish_harness.run()

    assert result.returncode != 0
    assert "PreconditionFailed" in result.stderr
    assert publish_harness.published(suffix) == b"published by somebody else"


def test_no_upload_in_the_publish_step_can_overwrite_by_construction() -> None:
    """Read as well as executed: every write is a conditional `put-object`, so
    there is no `aws s3 cp` upload left that would replace a published release
    whatever the branch above it decided."""
    lines = executable_lines(step_run(PUBLISH_STEP))
    uploads = anchored_lines(lines, r"^aws s3api put-object\b")
    put_object_line = anchored_line(step_run(PUBLISH_STEP), r"^aws s3api put-object\b")

    assert len(uploads) == 1, uploads
    assert '--if-none-match "*"' in put_object_line
    assert '--bucket "$RELEASE_BUCKET_NAME"' in put_object_line

    for line in lines:
        assert not re.match(r"^aws s3 cp\b.*s3://", line), line


def test_the_publish_step_reports_the_bucket_state_it_found() -> None:
    """Four presence states, and the operator reading a failed run needs to
    know which one it was before the diagnostics below make sense."""
    reported = anchored_line(step_run(PUBLISH_STEP), r"^echo \"Release \$RELEASE_SHA in s3://")

    assert "archive_published=$archive_published" in reported
    assert "checksum_published=$checksum_published" in reported


def test_existing_release_dispatch_verifies_s3_objects_before_ssm() -> None:
    verify_name = "Verify existing release for rollback"
    send_name = "Install release through SSM"

    assert step_if(verify_name) == "${{ steps.release.outputs.build != 'true' }}"
    assert step_index("Publish or verify release") < step_index(verify_name) < step_index(send_name)

    verify_run = step_run(verify_name)
    verify_lines = executable_lines(verify_run)
    assert 'archive_key="releases/$sha.tar.gz"' in verify_lines
    assert 'checksum_key="releases/$sha.tar.gz.sha256"' in verify_lines
    assert_rollback_head_object_guards(verify_run)

    send_run = step_run(send_name)
    assert (
        anchored_line(send_run, r'^command_id="\$\(aws ssm send-command\b')
        == 'command_id="$(aws ssm send-command --document-name "$DOCS_SSM_DOCUMENT_NAME" --instance-ids "$DOCS_INSTANCE_ID" --parameters "ReleaseSha=${sha}" --query \'Command.CommandId\' --output text)"'
    )
    assert step_if(send_name) is None


def test_rollback_guard_rejects_commented_out_head_object_commands() -> None:
    verify_run = step_run("Verify existing release for rollback")
    commented = with_line_replaced(
        verify_run,
        "if ! aws s3api head-object \\",
        "# if ! aws s3api head-object \\",
    )

    with pytest.raises(AssertionError):
        assert_rollback_head_object_guards(commented)


def test_rollback_guard_rejects_echo_and_noop_mentions_of_head_object_commands() -> None:
    verify_run = step_run("Verify existing release for rollback")
    inert = with_line_replaced(
        verify_run,
        "if ! aws s3api head-object \\",
        'echo "if ! aws s3api head-object --bucket \\"$RELEASE_BUCKET_NAME\\" --key \\"$checksum_key\\" > /dev/null 2>&1; then"\n: "aws s3api head-object --bucket \\"$RELEASE_BUCKET_NAME\\" --key \\"$checksum_key\\""',
    )

    with pytest.raises(AssertionError):
        assert_rollback_head_object_guards(inert)


def test_waiters_and_probes_use_only_the_release_sha_and_structured_checks() -> None:
    send_run = step_run("Install release through SSM")
    wait_run = step_run("Wait for installer")
    alb_run = step_run("Wait for healthy ALB target")
    probe_run = step_run("Verify public and protected routes")
    wait_lines = executable_lines(wait_run)
    alb_lines = executable_lines(alb_run)
    probe_lines = executable_lines(probe_run)

    assert (
        anchored_line(send_run, r'^command_id="\$\(aws ssm send-command\b')
        .count("ReleaseSha=")
        == 1
    )
    assert "aws ssm wait command-executed" not in wait_run
    assert 'deadline=$((SECONDS + 600))' in wait_lines
    assert 'poll_interval_seconds=5' in wait_lines
    assert "Pending|InProgress|Delayed)" in wait_run
    assert "Failed|TimedOut|Cancelled|Cancelling)" in wait_run
    assert "get-command-invocation never returned a status." in wait_run
    assert 'lookup_installer_status() {' in wait_run
    assert 'case "$status" in' in wait_run
    assert 'dump_invocation StandardOutputContent >&2' in wait_lines
    assert 'dump_invocation StandardErrorContent >&2' in wait_lines
    assert anchored_line(alb_run, r"^if ! aws elbv2 wait target-in-service\b").startswith(
        "if ! aws elbv2 wait target-in-service"
    )
    assert 'aws elbv2 describe-target-health --target-group-arn "$DOCS_TARGET_GROUP_ARN"' in alb_lines
    assert any("/privacy-policy/" in line for line in probe_lines)
    assert any("/guides/sso-integration-guide/" in line for line in probe_lines)
    assert sum("--max-redirs 0" in line for line in probe_lines) == 3
    assert any("%{http_code}" in line for line in probe_lines)
    assert any("/_auth/login" in line for line in probe_lines)
    assert sum('--connect-to "$connect_to"' in line for line in probe_lines) == 3

    for forbidden in ("OIDC_CLIENT_SECRET", "SESSION_SECRET", "client_secret", "session_secret"):
        assert forbidden not in send_run
        assert forbidden not in wait_run


def test_the_installer_wait_step_quotes_command_id_and_never_injects_it() -> None:
    wait_run = step_run("Wait for installer")
    wait_lines = executable_lines(wait_run)

    assert sum('--command-id "$command_id"' in line for line in wait_lines) >= 1
    for line in wait_lines:
        if line.startswith("aws ssm get-command-invocation"):
            assert '--command-id "$command_id"' in line
            assert "--command-id $command_id" not in line
    assert "eval" not in wait_run
    assert "`" not in wait_run


WAIT_STEP = "Wait for installer"
HARNESS_COMMAND_ID = "01234567-89ab-cdef-0123-456789abcdef"
HARNESS_INSTANCE_ID = "i-0123456789abcdef0"

FAKE_AWS_SSM = """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

calls = Path(os.environ["FAKE_AWS_CALLS"])
arguments = sys.argv[1:]

with calls.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(arguments) + "\\n")


def fail(message):
    print(message, file=sys.stderr)
    sys.exit(254)


def flags(tokens):
    parsed = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.startswith("--"):
            value = ""
            if index + 1 < len(tokens) and not tokens[index + 1].startswith("--"):
                value = tokens[index + 1]
                index += 1
            parsed[token] = value
        index += 1
    return parsed


LOOKUP_FAILURE = "__lookup_failure__"
EMPTY_STATUS = "__empty_status__"


if arguments[:2] == ["ssm", "get-command-invocation"]:
    parsed = flags(arguments[2:])
    schedule = json.loads(os.environ.get("FAKE_SSM_STATUS_SCHEDULE", '["Success"]'))
    poll_index = sum(
        1
        for line in calls.read_text(encoding="utf-8").splitlines()
        if json.loads(line)[:2] == ["ssm", "get-command-invocation"]
    ) - 1
    status = schedule[min(poll_index, len(schedule) - 1)]
    query = parsed.get("--query", "")

    if status == LOOKUP_FAILURE:
        print(
            "An error occurred (InvocationDoesNotExist) when calling the "
            "GetCommandInvocation operation: Invocation does not exist.",
            file=sys.stderr,
        )
        sys.exit(254)

    if status == EMPTY_STATUS:
        if query == "Status":
            print("")
        elif query == "ResponseCode":
            print("")
        elif query == "StandardOutputContent":
            print("")
        elif query == "StandardErrorContent":
            print("")
        else:
            fail(f"unsupported query {query!r}")
        sys.exit(0)

    payload = {
        "Status": status,
        "ResponseCode": 0 if status == "Success" else 1,
        "StandardOutputContent": "stdout-from-installer",
        "StandardErrorContent": "stderr-from-installer",
    }
    if query == "Status":
        print(status)
    elif query == "ResponseCode":
        print(payload["ResponseCode"])
    elif query == "StandardOutputContent":
        print(payload["StandardOutputContent"])
    elif query == "StandardErrorContent":
        print(payload["StandardErrorContent"])
    else:
        fail(f"unsupported query {query!r}")
    sys.exit(0)

fail(f"fatal error: unsupported invocation {arguments}")
"""

FAKE_SLEEP = """#!/usr/bin/env bash
exit 0
"""


@dataclass
class WaitHarness:
    tmp_path: Path
    script: Path = field(init=False)
    calls_file: Path = field(init=False)
    env: dict[str, str] = field(init=False)

    def __post_init__(self) -> None:
        fake_bin = self.tmp_path / "fake-bin"
        fake_bin.mkdir()
        self.calls_file = self.tmp_path / "aws-calls.jsonl"
        self.calls_file.write_text("", encoding="utf-8")
        PublishHarness._write_executable(fake_bin / "aws", FAKE_AWS_SSM)
        PublishHarness._write_executable(fake_bin / "sleep", FAKE_SLEEP)

        rendered = (
            step_run(WAIT_STEP)
            .replace(
                'command_id="${{ steps.command.outputs.id }}"',
                f'command_id="{HARNESS_COMMAND_ID}"',
            )
            .replace("deadline=$((SECONDS + 600))", "deadline=$((SECONDS + 2))")
        )
        self.script = self.tmp_path / "wait-step.sh"
        self.script.write_text(rendered, encoding="utf-8")

        self.env = {
            **os.environ,
            "DOCS_INSTANCE_ID": HARNESS_INSTANCE_ID,
            "FAKE_AWS_CALLS": str(self.calls_file),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
        }

    def with_status_schedule(self, *statuses: str) -> WaitHarness:
        self.env["FAKE_SSM_STATUS_SCHEDULE"] = json.dumps(list(statuses))
        return self

    def run(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(self.script)],
            cwd=self.tmp_path,
            capture_output=True,
            text=True,
            env=self.env,
            check=False,
        )


def test_the_installer_wait_step_polls_through_in_progress_statuses(tmp_path: Path) -> None:
    result = WaitHarness(tmp_path).with_status_schedule(
        "Pending", "InProgress", "Delayed", "Success"
    ).run()

    assert result.returncode == 0, result.stderr


def test_the_installer_wait_step_fails_on_terminal_ssm_status_with_diagnostics(
    tmp_path: Path,
) -> None:
    harness = WaitHarness(tmp_path)
    result = harness.with_status_schedule("Failed").run()

    assert result.returncode == 1
    assert f"SSM command {HARNESS_COMMAND_ID} ended with status Failed." in result.stderr
    assert "Response code: 1" in result.stderr
    assert "Installer stdout:" in result.stderr
    assert "stdout-from-installer" in result.stderr
    assert "Installer stderr:" in result.stderr
    assert "stderr-from-installer" in result.stderr


def test_the_installer_wait_step_times_out_with_a_clear_message(tmp_path: Path) -> None:
    harness = WaitHarness(tmp_path)
    result = harness.with_status_schedule("Pending").run()

    assert result.returncode == 1
    assert (
        f"SSM command {HARNESS_COMMAND_ID} did not finish within 600 seconds; last status: Pending."
        in result.stderr
    )
    assert "Installer stdout:" in result.stderr


def test_the_installer_wait_step_retries_initial_lookup_failures_until_success(
    tmp_path: Path,
) -> None:
    result = WaitHarness(tmp_path).with_status_schedule(
        "__lookup_failure__",
        "__lookup_failure__",
        "Success",
    ).run()

    assert result.returncode == 0, result.stderr


def test_the_installer_wait_step_times_out_when_lookup_never_succeeds(
    tmp_path: Path,
) -> None:
    harness = WaitHarness(tmp_path)
    result = harness.with_status_schedule("__lookup_failure__").run()

    assert result.returncode == 1
    assert (
        f"SSM command {HARNESS_COMMAND_ID} did not finish within 600 seconds; "
        "get-command-invocation never returned a status."
        in result.stderr
    )
    assert "last status:" not in result.stderr
    assert "Installer stdout:" in result.stderr


def test_the_installer_wait_step_times_out_when_status_stays_empty(tmp_path: Path) -> None:
    harness = WaitHarness(tmp_path)
    result = harness.with_status_schedule("__empty_status__").run()

    assert result.returncode == 1
    assert (
        f"SSM command {HARNESS_COMMAND_ID} did not finish within 600 seconds; "
        "get-command-invocation never returned a status."
        in result.stderr
    )
    assert "last status:" not in result.stderr


def test_route_probes_parse_the_canonical_https_origin_and_connect_directly_to_the_alb() -> None:
    verify_run = step_run("Verify required repository variables")
    probe_run = step_run("Verify public and protected routes")

    assert ': "${DOCS_ALB_DNS_NAME:?Set repository variable DOCS_ALB_DNS_NAME}"' in verify_run
    assert (
        'python - "$DOCS_PUBLIC_BASE_URL" "$DOCS_ALB_DNS_NAME" > "$probe_settings" <<\'PY\''
        in probe_run
    )
    for fragment in (
        'parsed = urlsplit(public_base_url)',
        'if parsed.scheme != "https":',
        "if parsed.username is not None or parsed.password is not None:",
        "if parsed.query or parsed.fragment:",
        'if parsed.hostname is None:',
        'if ":" in parsed.netloc:',
        'if not re.fullmatch(r"(?!-)[A-Za-z0-9-]{1,63}(?<!-)(?:\\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*", alb_dns_name):',
        'connect_to = f"{parsed.hostname}:443:{alb_dns_name}:443"',
        'print(f"connect_to={connect_to}")',
        'print(f"public_url={public_url}")',
        'print(f"protected_url={protected_url}")',
    ):
        assert fragment in probe_run

    public_probe = anchored_line(
        probe_run,
        r'^public_result="\$\(curl --silent --show-error --max-time 30 --max-redirs 0 --connect-to "\$connect_to" .* "\$public_url" \|\| true\)"$',
    )
    protected_probe = anchored_line(
        probe_run,
        r'^protected_status="\$\(curl --silent --show-error --max-time 30 --max-redirs 0 --connect-to "\$connect_to" .* "\$protected_url" \|\| true\)"$',
    )

    assert '--dump-header "$public_headers"' in public_probe
    assert '--write-out \'%{http_code} %{url_effective}\'' in public_probe
    assert '--dump-header "$protected_headers"' in protected_probe
    assert '--write-out \'%{http_code}\'' in protected_probe
    assert 'public_url="${base_url}/privacy-policy/"' not in probe_run
    assert 'protected_url="${base_url}/guides/sso-integration-guide/"' not in probe_run


ALB_DNS_NAME = "docs-alb-1234567890.us-east-1.elb.amazonaws.com"


def heredoc_bodies(run: str, tag: str = "PY") -> list[str]:
    """Every quoted-heredoc body in a `run:` block, in the order written.

    Reading the fragments of a program proves it is written down; running it is
    the only thing that proves what it accepts and what it refuses.
    """
    bodies: list[str] = []
    marker = f"<<'{tag}'"
    terminator = re.compile(rf"(?m)^{re.escape(tag)}$")
    offset = 0

    while (start := run.find(marker, offset)) != -1:
        body_start = run.index("\n", start) + 1
        end = terminator.search(run, body_start)
        assert end, f"a <<'{tag}' heredoc in this step is not terminated"
        bodies.append(run[body_start : end.start()])
        offset = end.end()

    return bodies


def test_no_embedded_program_is_wrapped_in_a_command_substitution() -> None:
    """Bash looks for the closing paren of a `$(...)` while still tracking
    quotes, and it does that across a quoted heredoc.

    So an apostrophe in a comment inside an embedded Python program opened a
    string that swallowed the rest of it, and whether the step parsed at all
    came down to whether the apostrophes happened to pair up. Both of these
    programs did, until one comment was added to each.

    Every one of them writes to a file and is read back from it. The redirection
    has no such rule, and `bash -n` over the rendered blocks is what proves the
    outcome -- this is what stops the construct coming back.
    """
    runs = [
        (str(candidate.get("name")), str(candidate["run"]))
        for candidate in STEPS
        if candidate.get("run")
    ]

    assert len(runs) >= 8, runs

    for step_name, run in runs:
        for match in re.finditer(r"\$\(", run):
            tail = run[match.start() :]
            paren = tail.index(")") if ")" in tail else len(tail)

            assert "<<'" not in tail[:paren], f"{step_name}: {tail[:80]!r}"


def probe_programs() -> list[str]:
    programs = heredoc_bodies(step_run("Verify public and protected routes"))

    assert len(programs) == 2, "the probe step is two Python programs"
    return programs


def probe_settings_program() -> str:
    program = probe_programs()[0]

    assert "connect_to=" in program
    return program


def login_redirect_program() -> str:
    program = probe_programs()[1]

    assert "code_challenge" in program
    return program


def run_probe_settings(
    public_base_url: str, alb_dns_name: str = ALB_DNS_NAME
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-", public_base_url, alb_dns_name],
        input=probe_settings_program(),
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_probe_parser_derives_settings_for_a_canonical_origin() -> None:
    completed = run_probe_settings("https://docs.authifi.io")

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines() == [
        f"connect_to=docs.authifi.io:443:{ALB_DNS_NAME}:443",
        "docs_host=docs.authifi.io",
        "public_url=https://docs.authifi.io/privacy-policy/",
        "protected_url=https://docs.authifi.io/guides/sso-integration-guide/",
        "login_url=https://docs.authifi.io/_auth/login",
        "callback_url=https://docs.authifi.io/_auth/callback",
    ]


@pytest.mark.parametrize(
    "public_base_url",
    ["https://198.51.100.7", "https://198.51.100.7/", "https://[2001:db8::1]"],
)
def test_the_probe_parser_refuses_an_ip_literal_origin(public_base_url: str) -> None:
    """`--connect-to` only rewrites the connection, never the request. An IP
    literal in `DOCS_PUBLIC_BASE_URL` would send the ALB a Host header the
    listener rules do not match and the certificate does not cover, so the
    probe would fail without saying why. It has to fail on the input instead.
    """
    completed = run_probe_settings(public_base_url)

    assert completed.returncode != 0
    assert "IP literal" in completed.stderr
    assert "connect_to=" not in completed.stdout


@pytest.mark.parametrize(
    "public_base_url",
    [
        "http://docs.authifi.io",
        "https://user:pass@docs.authifi.io",
        "https://docs.authifi.io?probe=1",
        "https://docs.authifi.io#fragment",
        "https://docs.authifi.io:444",
    ],
)
def test_the_probe_parser_refuses_origins_it_cannot_probe_faithfully(
    public_base_url: str,
) -> None:
    completed = run_probe_settings(public_base_url)

    assert completed.returncode != 0
    assert "connect_to=" not in completed.stdout


@pytest.mark.parametrize(
    "public_base_url",
    ["https://docs.authifi.io:443", "https://docs.authifi.io:443/"],
)
def test_the_probe_parser_refuses_an_explicitly_written_default_port(
    public_base_url: str,
) -> None:
    """`:443` is the default port, so the parser used to accept it and derive
    the same probe URLs -- but the value it accepted is one Terraform's
    `public_base_url` pattern refuses, and the server appends to
    `PUBLIC_BASE_URL` verbatim when it builds the callback URI.

    So a deployment configured this way would have the workflow declaring the
    origin fine while the plan refused it and the registered redirect URI did
    not match the one the server sent. The three readers agree on one shape:
    scheme, host, and at most the root.
    """
    completed = run_probe_settings(public_base_url)

    assert completed.returncode != 0
    assert "port" in completed.stderr.lower()
    assert "connect_to=" not in completed.stdout


def test_the_probe_parser_and_terraform_agree_on_every_origin_shape() -> None:
    """Neither is allowed to accept an origin the other refuses.

    The two are written in different languages against different inputs, which
    is exactly the pair that drifts. `DOCS_PUBLIC_BASE_URL` is a repository
    variable and `public_base_url` is a Terraform variable, and they name the
    same origin.
    """
    from server.tests.hcl_support import variable_accepts

    variables = (ROOT / "infra" / "variables.tf").read_text(encoding="utf-8")
    origins = [
        "https://docs.authifi.io",
        "https://docs.authifi.io/",
        "https://docs.authifi.io:443",
        "https://docs.authifi.io:443/",
        "https://docs.authifi.io:8443",
        "https://docs.authifi.io/docs",
        "https://docs.authifi.io?probe=1",
        "https://docs.authifi.io#fragment",
        "https://user:pass@docs.authifi.io",
        "http://docs.authifi.io",
        "https://198.51.100.7",
    ]

    for origin in origins:
        planned = variable_accepts(variables, "public_base_url", origin)
        probed = run_probe_settings(origin).returncode == 0

        assert planned == probed, origin


def test_the_probe_parser_treats_a_trailing_slash_as_the_root_it_is() -> None:
    completed = run_probe_settings("https://docs.authifi.io/")

    assert completed.returncode == 0, completed.stderr
    assert "public_url=https://docs.authifi.io/privacy-policy/" in completed.stdout


@pytest.mark.parametrize(
    "public_base_url",
    [
        "https://docs.authifi.io/docs",
        "https://docs.authifi.io/docs/",
        "https://docs.authifi.io//",
    ],
)
def test_the_probe_parser_refuses_a_base_url_carrying_a_path(
    public_base_url: str,
) -> None:
    """The parser used to strip the path and probe under the prefix anyway.

    Nothing is mounted beneath one -- the routes are rooted at `/` and the load
    balancer strips no prefix -- so `https://host/docs` produced a probe of
    `/docs/privacy-policy/`, which answers 404, and a deployment that fails its
    own checks for a reason the output does not explain. The server refuses the
    same value at startup, so the probe has to agree with it rather than
    quietly normalise it away.
    """
    completed = run_probe_settings(public_base_url)

    assert completed.returncode != 0
    assert "path" in completed.stderr
    assert "public_url=" not in completed.stdout


def test_the_probe_parser_derives_the_login_and_callback_urls_the_check_needs() -> None:
    completed = run_probe_settings("https://docs.authifi.io")

    assert completed.returncode == 0, completed.stderr
    assert "docs_host=docs.authifi.io" in completed.stdout
    assert "login_url=https://docs.authifi.io/_auth/login" in completed.stdout
    assert "callback_url=https://docs.authifi.io/_auth/callback" in completed.stdout


# --- Production OIDC discovery ------------------------------------------------
#
# `site_endpoint` never contacts the OIDC client, so a protected page answers
# its local 307 whether or not the configured issuer exists. With an
# unreachable issuer or a wrong discovery URL, the public probe stayed green,
# the protected probe stayed green, the workflow declared the deployment ready,
# and every reader then failed at the next `/_auth/login`. The authorization
# redirect is the only evidence a deployment has that discovery ran and that
# the private subnet's route out works.

DOCS_HOST = "docs.authifi.io"
CALLBACK_URL = f"https://{DOCS_HOST}/_auth/callback"

# Values a real redirect carries that must never reach a workflow log.
PROBE_STATE = "state-value-that-must-not-be-logged"
PROBE_NONCE = "nonce-value-that-must-not-be-logged"
PROBE_CHALLENGE = "challenge-value-that-must-not-be-logged"

AUTHORIZATION_PARAMETERS = {
    "client_id": "authifi-docs",
    "redirect_uri": CALLBACK_URL,
    "response_type": "code",
    "scope": "openid profile email",
    "state": PROBE_STATE,
    "nonce": PROBE_NONCE,
    "code_challenge": PROBE_CHALLENGE,
    "code_challenge_method": "S256",
}


def authorization_redirect(
    host: str = "login.authifi.io",
    scheme: str = "https",
    **overrides: str | None,
) -> str:
    """The `Location` an issuer's authorization endpoint redirect carries."""
    from urllib.parse import urlencode

    parameters = {**AUTHORIZATION_PARAMETERS}
    for name, value in overrides.items():
        if value is None:
            parameters.pop(name, None)
        else:
            parameters[name] = value

    return f"{scheme}://{host}/oauth2/authorize?{urlencode(parameters)}"


def run_login_redirect_check(
    location: str | None,
    tmp_path: Path,
    docs_host: str = DOCS_HOST,
    callback_url: str = CALLBACK_URL,
) -> str:
    """The verdict the probe step reads, from the headers curl would dump."""
    headers = tmp_path / "login-headers.txt"
    dumped = ["HTTP/2 302", "content-length: 0", "set-cookie: authifi-session=opaque; Path=/"]
    if location is not None:
        dumped.insert(1, f"location: {location}")
    headers.write_text("\r\n".join(dumped) + "\r\n\r\n", encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, "-", str(headers), docs_host, callback_url],
        input=login_redirect_program(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def test_a_real_authorization_redirect_passes_the_discovery_check(tmp_path: Path) -> None:
    assert run_login_redirect_check(authorization_redirect(), tmp_path) == "ok"


@pytest.mark.parametrize(
    "location",
    [
        None,
        "",
        # Relative, which is what our own login page would send.
        f"/_auth/login?next=%2F&state={PROBE_STATE}",
        # Our own host, which means discovery never reached the issuer.
        authorization_redirect(host=DOCS_HOST),
        authorization_redirect(host=DOCS_HOST.upper()),
        # Not TLS to the issuer.
        authorization_redirect(scheme="http"),
        # Present but empty is the same as absent for every one of these.
        authorization_redirect(client_id=None),
        authorization_redirect(client_id=""),
        authorization_redirect(redirect_uri=None),
        authorization_redirect(redirect_uri=""),
        authorization_redirect(state=None),
        authorization_redirect(state=""),
        authorization_redirect(nonce=None),
        authorization_redirect(nonce=""),
        authorization_redirect(code_challenge=None),
        authorization_redirect(code_challenge=""),
        # The wrong flow, or PKCE downgraded to the mode S256 exists to replace.
        authorization_redirect(response_type="token"),
        authorization_redirect(response_type="code id_token"),
        authorization_redirect(code_challenge_method="plain"),
        authorization_redirect(code_challenge_method=None),
        # An authorization endpoint told to send the code somewhere else.
        authorization_redirect(redirect_uri="https://attacker.example/callback"),
    ],
)
def test_a_redirect_that_does_not_prove_discovery_ran_is_refused(
    location: str | None, tmp_path: Path
) -> None:
    verdict = run_login_redirect_check(location, tmp_path)

    assert verdict != "ok"
    assert verdict, "a refusal has to say something an operator can act on"


@pytest.mark.parametrize(
    "location",
    [
        authorization_redirect(),
        authorization_redirect(host=DOCS_HOST),
        authorization_redirect(scheme="http"),
        authorization_redirect(code_challenge_method="plain"),
        authorization_redirect(response_type="token"),
        f"/_auth/login?state={PROBE_STATE}&nonce={PROBE_NONCE}",
    ],
)
def test_the_verdict_never_carries_the_redirect_it_is_judging(
    location: str, tmp_path: Path
) -> None:
    """The check runs against the live production deployment and its output
    lands in a workflow log.

    `state` and `nonce` are single-use anti-forgery material for a transaction
    nobody completes, and the PKCE challenge is a digest of a verifier this
    probe throws away, so none of it is worth much to an attacker. It is worth
    nothing at all if it never leaves the runner, which costs only naming the
    parameters rather than echoing them -- and the same restraint is what keeps
    the whole URL, redirect chain included, out of the log.
    """
    verdict = run_login_redirect_check(location, tmp_path)

    for secret in (PROBE_STATE, PROBE_NONCE, PROBE_CHALLENGE):
        assert secret not in verdict
    assert location not in verdict


def test_the_redaction_assertions_are_not_passing_on_an_empty_verdict() -> None:
    """A redaction check is vacuous if the thing it inspects is empty, and it
    would stay green if the verdict were silenced rather than sanitised.

    So the material has to actually be in the input those tests feed, and the
    verdict has to actually say something. Without this, "the secret is not in
    the output" is satisfied by no output at all.
    """
    location = authorization_redirect(host=DOCS_HOST)

    for secret in (PROBE_STATE, PROBE_NONCE, PROBE_CHALLENGE):
        assert secret in location


@pytest.mark.parametrize(
    "headers",
    [
        # Header dumps curl can legitimately produce, and one it cannot.
        "",
        "HTTP/2 302\r\n\r\n",
        "not a header block at all",
        "location\r\n\r\n",
        "\x00\x01\x02 binary",
    ],
)
def test_the_verifier_answers_rather_than_crashing_on_any_header_dump(
    headers: str, tmp_path: Path
) -> None:
    """The verifier's output is captured into a shell variable that decides
    whether the deployment is ready.

    An unhandled traceback would print nothing to stdout, so the verdict would
    be the empty string -- which is not `ok`, so the loop would retry, and the
    failure an operator finally sees would be "and " with the reason on a
    stream nobody correlated. It has to answer.
    """
    path = tmp_path / "login-headers.txt"
    path.write_text(headers, encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, "-", str(path), DOCS_HOST, CALLBACK_URL],
        input=login_redirect_program(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip(), "the verifier produced no verdict"
    assert completed.stdout.strip() != "ok"


def test_a_verifier_that_cannot_run_fails_the_check_instead_of_the_step() -> None:
    """`login_verdict="$(python ...)"` under `set -e` ended the step on the
    spot if the interpreter itself failed -- a missing file, an OOM kill -- so
    a transient fault during the retry window became an immediate abort while
    every other probe in the same loop retried.

    Worse, it aborted without the diagnostics: the deadline branch is what
    prints them, and this exited before reaching it.
    """
    probe_run = step_run("Verify public and protected routes")
    verdict_line = anchored_line(probe_run, r'^login_verdict="\$\(')

    assert "|| true" in verdict_line or "||" in verdict_line, verdict_line

    lines = executable_lines(probe_run)

    # And an empty verdict has to read as a refusal an operator can act on,
    # not as an empty string interpolated into a sentence.
    assert any("login_verdict" in line and "verdict" in line.lower()
               and (":-" in line or "-unavailable" in line) for line in lines), lines


def test_every_probe_temp_file_is_cleaned_up_rather_than_leaked() -> None:
    """Three `mktemp` calls inside a loop that runs every ten seconds for five
    minutes: up to ninety files on the runner, and the two dumps that carry the
    login `Location` among them.

    They are small and the runner is ephemeral, so this is hygiene rather than
    a leak that matters -- but the header dump holding this transaction's state
    and nonce is one worth removing on purpose rather than leaving for the VM
    teardown.
    """
    probe_run = step_run("Verify public and protected routes")
    lines = executable_lines(probe_run)

    created = [line for line in lines if "mktemp" in line]

    assert created, "the probe step creates no temp files"

    # Every file made inside the loop is removed on the way round it, and the
    # removal covers the login header dump by name.
    assert any(re.search(r"^rm -f .*login_headers", line) for line in lines), lines
    assert any(re.search(r"^\s*trap .*(cleanup|rm -f)", line) for line in lines) or any(
        re.search(r"^rm -f ", line) for line in lines
    ), lines

    # The verifier program itself is written once, outside the loop, so it is
    # removed once, on the way out of the step.
    assert any("login_check" in line and line.startswith("trap ") for line in lines), lines


def test_the_login_probe_is_credential_free_and_does_not_follow_redirects() -> None:
    """It has to exercise discovery and the NAT route out, and nothing else.

    No cookie jar, no credentials, and no `--location`: following the redirect
    would put a request to the issuer's authorization endpoint inside a
    deployment check, which is somebody else's availability deciding whether
    ours passes.
    """
    probe_run = step_run("Verify public and protected routes")
    login_probe = anchored_line(probe_run, r'^login_status="\$\(curl ')

    assert '--connect-to "$connect_to"' in login_probe
    assert "--max-redirs 0" in login_probe
    assert '--dump-header "$login_headers"' in login_probe
    assert '"$login_url"' in login_probe
    for forbidden in ("--location", "--cookie", "--user", "--header 'Authorization", "-L "):
        assert forbidden not in login_probe

    # The whole step, so no other request grows credentials either.
    for forbidden in ("OIDC_CLIENT_SECRET", "SESSION_SECRET", "--cookie", "--user "):
        assert forbidden not in probe_run


def test_the_login_response_headers_are_never_dumped_into_the_log() -> None:
    """The public and protected header dumps are printed on failure, because
    nothing in them is secret. The login response's `Location` carries this
    transaction's `state` and `nonce`, so its diagnostics name the verdict
    instead."""
    probe_run = step_run("Verify public and protected routes")
    lines = executable_lines(probe_run)

    assert 'cat "$public_headers" >&2' in lines
    assert 'cat "$protected_headers" >&2' in lines
    assert 'cat "$login_headers" >&2' not in lines
    assert not any('login_headers' in line and line.startswith("cat ") for line in lines)
    assert any('$login_verdict' in line for line in lines)


def test_the_deployment_is_not_declared_ready_without_the_discovery_check() -> None:
    """The success condition, read as one expression: a green public probe and
    a green protected probe are no longer enough to break out of the loop."""
    condition = anchored_line(step_run("Verify public and protected routes"), r"^if \[\[ ")

    assert '"$public_status" == "200"' in condition
    assert '"$protected_status" == "307"' in condition
    assert '"$login_verdict" == ok' in condition
    assert re.search(r'"\$login_status" =~ \^30', condition)


def test_the_probe_parser_refuses_an_alb_name_carrying_shell_syntax() -> None:
    completed = run_probe_settings("https://docs.authifi.io", "$(id).example.com")

    assert completed.returncode != 0
    assert "DOCS_ALB_DNS_NAME" in completed.stderr


def test_structural_parsing_ignores_comments_and_dead_strings() -> None:
    poisoned = (
        RAW_WORKFLOW
        + "\n# - name: Verify existing release for rollback\n"
        + "#   if: ${{ steps.release.outputs.build != 'true' }}\n"
        + "#   run: aws s3 cp /tmp/archive /tmp/checksum && aws ssm send-command\n"
        + "dead_text: \"workflow_dispatch release_sha docker apprunner ecr\"\n"
    )

    parsed = parse_steps(poisoned)

    assert [candidate["name"] for candidate in parsed] == [candidate["name"] for candidate in STEPS]
    assert next(
        candidate for candidate in parsed if candidate["name"] == "Verify existing release for rollback"
    )["if"] == step_if("Verify existing release for rollback")


def test_obsolete_deploy_tooling_is_absent_from_parsed_actions_and_shell() -> None:
    used_actions = {
        str(candidate["uses"]).split("@", 1)[0].lower()
        for candidate in STEPS
        if "uses" in candidate
    }
    shell_text = "\n".join(step_run(candidate["name"]) for candidate in STEPS).lower()

    assert "aws-actions/amazon-ecr-login" not in used_actions
    for forbidden in ("self-hosted", "docker ", "docker/", "buildx", "ghcr.io", "apprunner"):
        assert forbidden not in shell_text
    assert re.search(r"(^|[^a-z0-9])ecr([^a-z0-9]|$)", shell_text) is None
