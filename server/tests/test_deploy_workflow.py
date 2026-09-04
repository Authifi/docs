from __future__ import annotations

from pathlib import Path
import re

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


def test_push_and_rerun_build_steps_keep_immutable_checksum_behavior() -> None:
    assert step_if("Build release") == "${{ steps.release.outputs.build == 'true' }}"
    assert step_if("Publish or verify release") == "${{ steps.release.outputs.build == 'true' }}"

    build_run = step_run("Build release")
    publish_run = step_run("Publish or verify release")
    publish_lines = executable_lines(publish_run)

    assert 'set -euo pipefail' in executable_lines(build_run)
    assert (
        anchored_line(
            build_run,
            r'^\.\/scripts\/build-release\.sh "\$\{\{ steps\.release\.outputs\.sha \}\}" dist/releases$',
        )
        == './scripts/build-release.sh "${{ steps.release.outputs.sha }}" dist/releases'
    )
    assert (
        anchored_line(
            publish_run,
            r'^if aws s3api head-object --bucket "\$RELEASE_BUCKET_NAME" --key "releases/\$sha\.tar\.gz" > /dev/null 2>&1; then$',
        )
        == 'if aws s3api head-object --bucket "$RELEASE_BUCKET_NAME" --key "releases/$sha.tar.gz" > /dev/null 2>&1; then'
    )
    assert (
        anchored_line(
            publish_run,
            r'^aws s3api head-object --bucket "\$RELEASE_BUCKET_NAME" --key "releases/\$sha\.tar\.gz\.sha256" > /dev/null$',
        )
        == 'aws s3api head-object --bucket "$RELEASE_BUCKET_NAME" --key "releases/$sha.tar.gz.sha256" > /dev/null'
    )
    assert 'aws s3 cp "$prefix/$sha.tar.gz.sha256" "$remote_checksum"' in publish_lines
    assert 'sha256sum --check "$(basename "$remote_checksum")"' in publish_lines
    assert 'echo "checksum mismatch for existing release" >&2' in publish_lines
    assert 'aws s3 cp "dist/releases/$sha.tar.gz" "$prefix/$sha.tar.gz"' in publish_lines
    assert 'aws s3 cp "dist/releases/$sha.tar.gz.sha256" "$prefix/$sha.tar.gz.sha256"' in publish_lines


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
