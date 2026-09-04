from __future__ import annotations

from pathlib import Path

WORKFLOW = (
    Path(__file__).resolve().parents[2] / ".github" / "workflows" / "deploy.yml"
).read_text(encoding="utf-8")
LOWERED = WORKFLOW.lower()


def test_production_deploy_uses_github_oidc_s3_and_ssm() -> None:
    assert "permissions:" in WORKFLOW
    assert "id-token: write" in WORKFLOW
    assert "environment: production" in WORKFLOW
    assert (
        "aws-actions/configure-aws-credentials@7474bc4690e29a8392af63c5b98e7449536d5c3a"
        in WORKFLOW
    )
    assert "./scripts/build-release.sh" in WORKFLOW
    assert "aws s3api head-object" in WORKFLOW
    assert "aws s3 cp" in WORKFLOW
    assert "aws ssm send-command" in WORKFLOW
    assert "aws ssm wait command-executed" in WORKFLOW
    assert "aws ssm get-command-invocation" in WORKFLOW
    assert "aws elbv2 wait target-in-service" in WORKFLOW


def test_required_repository_variables_are_checked_before_aws_mutation() -> None:
    verify_step = "name: Verify required repository variables"
    configure_step = "name: Configure AWS credentials"

    assert verify_step in WORKFLOW
    assert configure_step in WORKFLOW
    assert WORKFLOW.index(verify_step) < WORKFLOW.index(configure_step)

    for variable in (
        "AWS_REGION",
        "AWS_DEPLOY_ROLE_ARN",
        "RELEASE_BUCKET_NAME",
        "DOCS_INSTANCE_ID",
        "DOCS_SSM_DOCUMENT_NAME",
        "DOCS_TARGET_GROUP_ARN",
        "DOCS_PUBLIC_BASE_URL",
    ):
        assert f': "${{{variable}:?Set repository variable {variable}}}"' in WORKFLOW


def test_obsolete_container_and_app_runner_flow_is_absent() -> None:
    for forbidden in (
        "self-hosted",
        "docker ",
        "docker/",
        "buildx",
        "amazon-ecr-login",
        "ghcr.io",
        "ecr",
        "apprunner",
    ):
        assert forbidden not in LOWERED


def test_rerun_reuses_only_an_identical_release_and_dispatch_can_roll_back() -> None:
    assert "workflow_dispatch:" in WORKFLOW
    assert "release_sha:" in WORKFLOW
    assert "inputs.release_sha" in WORKFLOW
    assert "GITHUB_SHA" in WORKFLOW or "github.sha" in WORKFLOW
    assert "--key \"releases/$sha.tar.gz\"" in WORKFLOW
    assert "sha256sum --check" in WORKFLOW
    assert "checksum mismatch for existing release" in WORKFLOW
    assert "ReleaseSha=${sha}" in WORKFLOW or "ReleaseSha=${release_sha}" in WORKFLOW


def test_live_probe_checks_public_and_protected_boundaries_without_redirect_following() -> None:
    assert "/privacy-policy/" in WORKFLOW
    assert "/guides/sso-integration-guide/" in WORKFLOW
    assert "--max-redirs 0" in WORKFLOW
    assert "%{http_code}" in WORKFLOW
    assert "307" in WORKFLOW
    assert "/_auth/login" in WORKFLOW


def test_failed_ssm_commands_dump_output_without_passing_any_secret_values() -> None:
    assert "StandardOutputContent" in WORKFLOW
    assert "StandardErrorContent" in WORKFLOW
    assert "ReleaseSha=" in WORKFLOW
    for forbidden in ("OIDC_CLIENT_SECRET", "SESSION_SECRET", "client_secret", "session_secret"):
        assert forbidden not in WORKFLOW
