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

RAW_WORKFLOW = (
    Path(__file__).resolve().parents[2] / ".github" / "workflows" / "deploy.yml"
).read_text(encoding="utf-8")
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
    assert anchored_line(wait_run, r"^if ! aws ssm wait command-executed\b").startswith(
        "if ! aws ssm wait command-executed"
    )
    assert 'dump_invocation StandardOutputContent >&2' in wait_lines
    assert 'dump_invocation StandardErrorContent >&2' in wait_lines
    assert anchored_line(alb_run, r"^if ! aws elbv2 wait target-in-service\b").startswith(
        "if ! aws elbv2 wait target-in-service"
    )
    assert 'aws elbv2 describe-target-health --target-group-arn "$DOCS_TARGET_GROUP_ARN"' in alb_lines
    assert any("/privacy-policy/" in line for line in probe_lines)
    assert any("/guides/sso-integration-guide/" in line for line in probe_lines)
    assert sum("--max-redirs 0" in line for line in probe_lines) == 2
    assert any("%{http_code}" in line for line in probe_lines)
    assert any("/_auth/login" in line for line in probe_lines)
    assert sum('--connect-to "$connect_to"' in line for line in probe_lines) == 2

    for forbidden in ("OIDC_CLIENT_SECRET", "SESSION_SECRET", "client_secret", "session_secret"):
        assert forbidden not in send_run
        assert forbidden not in wait_run


def test_route_probes_parse_the_canonical_https_origin_and_connect_directly_to_the_alb() -> None:
    verify_run = step_run("Verify required repository variables")
    probe_run = step_run("Verify public and protected routes")

    assert ': "${DOCS_ALB_DNS_NAME:?Set repository variable DOCS_ALB_DNS_NAME}"' in verify_run
    assert 'probe_settings="$(python - <<\'PY\' "$DOCS_PUBLIC_BASE_URL" "$DOCS_ALB_DNS_NAME"' in probe_run
    for fragment in (
        'parsed = urlsplit(public_base_url)',
        'if parsed.scheme != "https":',
        "if parsed.username is not None or parsed.password is not None:",
        "if parsed.query or parsed.fragment:",
        'if parsed.hostname is None:',
        'if parsed.port not in (None, 443):',
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


def probe_settings_program() -> str:
    """The Python the probe step feeds to the interpreter on its heredoc.

    Reading the fragments of this program proves it is written down; running it
    is the only thing that proves what it accepts and what it refuses.
    """
    run = step_run("Verify public and protected routes")
    body = run[run.index("\n", run.index("<<'PY'")) + 1 :]
    terminator = re.search(r"(?m)^PY$", body)

    assert terminator, "the probe heredoc is not terminated"
    return body[: terminator.start()]


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
        "public_url=https://docs.authifi.io/privacy-policy/",
        "protected_url=https://docs.authifi.io/guides/sso-integration-guide/",
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
    for forbidden in ("self-hosted", "docker ", "docker/", "buildx", "ghcr.io", "ecr", "apprunner"):
        assert forbidden not in shell_text
