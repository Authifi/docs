"""Guards on the deployment role's permission to hand App Runner its access role.

`deploy.yml` reads the live service configuration and posts it back to
`apprunner update-service`. That payload carries
`SourceConfiguration.AuthenticationConfiguration.AccessRoleArn`, so the call is
a role hand-off and App Runner refuses it without `iam:PassRole`. The grant has
to name that one role and no other, confined to the service that actually
assumes it.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MAIN_TF = REPO_ROOT / "infra" / "main.tf"
DEPLOY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy.yml"

ACCESS_ROLE_ARN = "aws_iam_role.apprunner_access.arn"
# App Runner assumes the ECR access role as its *build* service, not as the
# `tasks.` principal that runs the container. `iam:PassedToService` has to match
# whichever principal the role's own trust policy names.
BUILD_SERVICE_PRINCIPAL = "build.apprunner.amazonaws.com"


def block_body(source: str, opening_brace: int) -> str:
    """The text of an HCL block, from the brace that opens it to its match."""
    depth = 0
    for offset, character in enumerate(source[opening_brace:], start=opening_brace):
        depth += {"{": 1, "}": -1}.get(character, 0)
        if depth == 0:
            return source[opening_brace + 1 : offset]
    raise AssertionError(f"unbalanced braces from offset {opening_brace}")


def hcl_block(source: str, header: str) -> str:
    return block_body(source, source.index(header) + len(header) - 1)


def statements(document_name: str) -> list[str]:
    body = hcl_block(
        MAIN_TF.read_text(encoding="utf-8"),
        f'data "aws_iam_policy_document" "{document_name}" {{',
    )
    return [block_body(body, match.end() - 1) for match in re.finditer(r"\n  statement \{", body)]


def statement_with_action(document_name: str, action: str) -> str:
    matches = [block for block in statements(document_name) if f'"{action}"' in block]
    assert len(matches) == 1, f"expected exactly one {action} statement, found {len(matches)}"
    return matches[0]


def test_the_deploy_role_may_pass_the_app_runner_access_role() -> None:
    statement = statement_with_action("github_deploy", "iam:PassRole")

    assert re.search(r"effect\s*=\s*\"Allow\"", statement)
    assert ACCESS_ROLE_ARN in statement


def test_passing_a_role_is_confined_to_app_runners_build_service() -> None:
    """Unconstrained `iam:PassRole` is a privilege-escalation primitive."""
    statement = statement_with_action("github_deploy", "iam:PassRole")

    condition = hcl_block(statement, "condition {")
    assert re.search(r"test\s*=\s*\"StringEquals\"", condition), condition
    assert re.search(r"variable\s*=\s*\"iam:PassedToService\"", condition), condition
    assert f'"{BUILD_SERVICE_PRINCIPAL}"' in condition, condition


def test_the_condition_names_the_service_that_actually_assumes_the_role() -> None:
    """A trust-policy change has to break the condition rather than outlive it.

    If the access role were ever retrusted to another principal, a stale
    `iam:PassedToService` would silently deny every deployment.
    """
    trust = hcl_block(
        MAIN_TF.read_text(encoding="utf-8"),
        'data "aws_iam_policy_document" "apprunner_access_assume_role" {',
    )

    assert re.findall(r"identifiers\s*=\s*\[\"([^\"]+)\"\]", trust) == [BUILD_SERVICE_PRINCIPAL]


def test_no_other_role_can_be_passed() -> None:
    """One resource, named literally. `"*"` here would let the deploy role hand
    any role in the account to any service that will take it."""
    statement = statement_with_action("github_deploy", "iam:PassRole")

    resources = re.search(r"resources\s*=\s*\[(?P<items>[^\]]*)\]", statement)
    assert resources, statement
    named = [item.strip().rstrip(",") for item in resources["items"].split("\n") if item.strip()]
    assert named == [ACCESS_ROLE_ARN]


def test_the_pass_role_grant_is_not_repeated_elsewhere() -> None:
    assert MAIN_TF.read_text(encoding="utf-8").count('"iam:PassRole"') == 1


def test_the_deploy_workflow_still_hands_the_access_role_back() -> None:
    """The reason for the grant, pinned: remove this and the grant is dead weight."""
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    assert "apprunner update-service" in workflow
    assert "--source-configuration" in workflow
    assert ".Service.SourceConfiguration" in workflow
    # The payload is forwarded whole, which is what carries AccessRoleArn.
    assert "AuthenticationConfiguration" not in workflow, (
        "the workflow now touches AuthenticationConfiguration; recheck what it passes"
    )
