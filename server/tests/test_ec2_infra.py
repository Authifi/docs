"""Guards on the private-EC2 hosting root.

The docs server runs on one EC2 instance in a private subnet behind a public
ALB, and every deployment reaches it through Systems Manager rather than SSH or
a public address. None of that is verifiable by `terraform validate`, which
checks syntax and provider schemas but has nothing to say about whether the
instance is reachable from the internet or whether the deploy role can do more
than deploy. These tests read the committed HCL and assert the properties the
architecture depends on.
"""

from __future__ import annotations

import re
from pathlib import Path

from server.tests.hcl_support import hcl_block, statement_with_action

ROOT = Path(__file__).resolve().parents[2]
MAIN = (ROOT / "infra" / "main.tf").read_text(encoding="utf-8")
VARIABLES = (ROOT / "infra" / "variables.tf").read_text(encoding="utf-8")
OUTPUTS = (ROOT / "infra" / "outputs.tf").read_text(encoding="utf-8")
USER_DATA = (ROOT / "infra" / "templates" / "user-data.sh.tftpl").read_text(encoding="utf-8")
TFVARS_EXAMPLE = (ROOT / "infra" / "terraform.tfvars.example").read_text(encoding="utf-8")


def attribute(body: str, name: str) -> str | None:
    """One top-level `name = value` from a block body, whitespace normalised.

    The exact-text assertions below pin `terraform fmt` alignment where the
    alignment is itself worth pinning. Everywhere else, reading the value is
    what makes an assertion about behaviour rather than about formatting.
    """
    match = re.search(rf"^\s*{re.escape(name)}\s*=\s*(.+?)\s*$", body, re.MULTILINE)
    return match[1] if match else None


def security_group_rules(direction: str) -> dict[str, str]:
    header = f'resource "aws_vpc_security_group_{direction}_rule"'
    return {
        name: hcl_block(MAIN, f'{header} "{name}"')
        for name in re.findall(rf'{re.escape(header)} "([^"]+)"', MAIN)
    }


# --- The App Runner architecture is gone, not merely unused ------------------


def test_app_runner_and_ecr_are_absent() -> None:
    assert 'resource "aws_apprunner_' not in MAIN
    assert 'resource "aws_ecr_' not in MAIN
    assert "apprunner" not in VARIABLES.lower()
    assert "ecr_" not in VARIABLES.lower()


# --- The instance is private ---------------------------------------------------


def test_instance_is_private_and_uses_encrypted_ebs() -> None:
    instance = hcl_block(MAIN, 'resource "aws_instance" "docs"')
    assert "associate_public_ip_address = false" in instance
    root = hcl_block(instance, "root_block_device")
    assert "encrypted = true" in root


def test_the_instance_requires_imdsv2() -> None:
    """IMDSv1 lets any SSRF in the docs server read the instance role's
    credentials with a single unauthenticated GET."""
    metadata = hcl_block(hcl_block(MAIN, 'resource "aws_instance" "docs"'), "metadata_options")

    assert attribute(metadata, "http_tokens") == '"required"'
    assert attribute(metadata, "http_put_response_hop_limit") == "1"


def test_the_app_subnet_has_no_route_off_the_vpc() -> None:
    """The private route table's emptiness is the containment, so it is pinned.

    A second `aws_route` is how a NAT or gateway route gets added without anyone
    revisiting whether the instance should be able to dial out.
    """
    routes = re.findall(r'resource "aws_route" "([^"]+)"', MAIN)

    assert routes == ["public_internet"]
    assert attribute(hcl_block(MAIN, 'resource "aws_route" "public_internet"'), "route_table_id") == (
        "aws_route_table.public.id"
    )
    assert 'resource "aws_nat_gateway"' not in MAIN


def test_the_instance_is_registered_with_the_target_group() -> None:
    """Without this the ALB has no targets and every request is a 503."""
    attachment = hcl_block(MAIN, 'resource "aws_lb_target_group_attachment" "docs"')

    assert attribute(attachment, "target_group_arn") == "aws_lb_target_group.docs.arn"
    assert attribute(attachment, "target_id") == "aws_instance.docs.id"
    assert attribute(attachment, "port") == "var.app_port"


# --- Only the load balancer can reach the application ------------------------


def test_only_the_alb_security_group_can_reach_the_app_port() -> None:
    ingress = hcl_block(MAIN, 'resource "aws_vpc_security_group_ingress_rule" "app_from_alb"')
    assert "referenced_security_group_id = aws_security_group.alb.id" in ingress
    assert "from_port                    = var.app_port" in ingress
    assert "cidr_ipv4" not in ingress


def test_the_app_has_no_unrestricted_egress() -> None:
    """Egress is the exfiltration path out of a subnet with no inbound door."""
    app_egress = [
        body
        for body in security_group_rules("egress").values()
        if attribute(body, "security_group_id") == "aws_security_group.app.id"
    ]

    assert app_egress, "the app security group has no egress rules to check"
    for body in app_egress:
        assert "0.0.0.0/0" not in body, body
        assert "::/0" not in body, body


def test_the_app_reaches_s3_through_the_gateway_endpoints_prefix_list() -> None:
    """Gateway-endpoint traffic keeps S3's public addresses, so a security group
    that only allows the interface endpoints silently breaks every download."""
    to_s3 = hcl_block(MAIN, 'resource "aws_vpc_security_group_egress_rule" "app_to_s3"')

    assert attribute(to_s3, "prefix_list_id") == "aws_vpc_endpoint.s3.prefix_list_id"
    assert attribute(to_s3, "from_port") == "443"


def test_the_endpoint_security_group_admits_only_the_app() -> None:
    ingress = hcl_block(MAIN, 'resource "aws_vpc_security_group_ingress_rule" "endpoints_from_app"')

    assert attribute(ingress, "referenced_security_group_id") == "aws_security_group.app.id"
    assert attribute(ingress, "from_port") == "443"
    assert "cidr_ipv4" not in ingress


# --- The public edge ----------------------------------------------------------


def test_alb_redirects_http_and_checks_application_health() -> None:
    http = hcl_block(MAIN, 'resource "aws_lb_listener" "http"')
    target = hcl_block(MAIN, 'resource "aws_lb_target_group" "docs"')
    assert 'status_code = "HTTP_301"' in http
    assert 'path                = "/health"' in hcl_block(target, "health_check")


def test_https_is_served_only_once_the_certificate_is_enabled() -> None:
    """DNS lives outside this root, so the first apply cannot have a validated
    certificate. The bootstrap listener holds port 80 until it does, and the two
    counts are exact inverses so port 80 is never claimed twice or left unbound.
    """
    counts = {
        name: attribute(hcl_block(MAIN, f'resource "aws_lb_listener" "{name}"'), "count")
        for name in ("http", "https", "bootstrap")
    }

    assert counts["http"] == "var.enable_https_listener ? 1 : 0"
    assert counts["https"] == "var.enable_https_listener ? 1 : 0"
    assert counts["bootstrap"] == "var.enable_https_listener ? 0 : 1"


def test_the_certificate_is_not_blocked_on_validation_at_apply_time() -> None:
    """`aws_acm_certificate_validation` waits for records this root cannot
    create, so its presence would deadlock the very first apply."""
    assert 'resource "aws_acm_certificate_validation"' not in MAIN
    assert 'output "certificate_validation_records"' in OUTPUTS


# --- Deployment reaches the instance without SSH or a public address ---------


def test_private_endpoints_cover_release_and_ssm_traffic() -> None:
    assert 'resource "aws_vpc_endpoint" "s3"' in MAIN
    for name in ("ssm", "ssmmessages", "ec2messages"):
        endpoint = hcl_block(MAIN, f'resource "aws_vpc_endpoint" "{name}"')
        assert 'vpc_endpoint_type = "Interface"' in endpoint
        assert "private_dns_enabled = true" in endpoint


def test_ssm_document_stages_both_objects_before_running_the_installer() -> None:
    document = hcl_block(MAIN, 'resource "aws_ssm_document" "deploy"')
    assert document.count('"aws:downloadContent"') == 2
    assert ".tar.gz" in document
    assert ".tar.gz.sha256" in document
    assert '"aws:runShellScript"' in document
    assert "/usr/local/sbin/authifi-docs-deploy" in document


def test_ssm_stages_the_release_into_the_directory_the_installer_reads() -> None:
    """`aws:downloadContent` treats a destination that is not a directory as the
    *filename* to save to, so a path without a trailing separator would land the
    archive at `incoming/<sha>` and then let the checksum step overwrite it.

    The installer reads `incoming/<sha>/<sha>.tar.gz`, so the destination has to
    name a directory.
    """
    document = hcl_block(MAIN, 'resource "aws_ssm_document" "deploy"')

    destinations = re.findall(r'destinationPath\s*=\s*"([^"]+)"', document)

    assert len(destinations) == 2
    assert set(destinations) == {"/opt/authifi-docs/incoming/{{ ReleaseSha }}/"}


def test_the_release_sha_is_the_only_thing_a_deploy_can_inject() -> None:
    """The SHA is interpolated straight into a root shell command, so the
    document's own pattern is the boundary that keeps it a SHA."""
    document = hcl_block(MAIN, 'resource "aws_ssm_document" "deploy"')

    assert 'allowedPattern = "^[0-9a-f]{40}$"' in document

    # Two source paths, two destinations, and the installer argument.
    placeholders = re.findall(r"\{\{ ?[A-Za-z]+ ?\}\}", document)
    assert set(placeholders) == {"{{ ReleaseSha }}"}
    assert len(placeholders) == 5


def test_the_instance_role_reads_only_the_release_prefix() -> None:
    managed = hcl_block(MAIN, 'resource "aws_iam_role_policy_attachment" "instance_ssm_core"')
    policy = hcl_block(MAIN, 'data "aws_iam_policy_document" "instance_releases"')
    statement = statement_with_action(policy, "s3:GetObject")

    assert "AmazonSSMManagedInstanceCore" in managed
    assert attribute(statement, "resources") == '["${aws_s3_bucket.releases.arn}/releases/*"]'
    assert "s3:PutObject" not in policy
    assert "s3:DeleteObject" not in policy


# --- The release bucket -------------------------------------------------------


def test_release_bucket_is_private_encrypted_versioned_and_expiring() -> None:
    assert 'resource "aws_s3_bucket_public_access_block" "releases"' in MAIN
    assert 'resource "aws_s3_bucket_server_side_encryption_configuration" "releases"' in MAIN
    assert 'resource "aws_s3_bucket_versioning" "releases"' in MAIN
    assert 'resource "aws_s3_bucket_lifecycle_configuration" "releases"' in MAIN


def test_every_public_access_block_flag_is_set() -> None:
    """Three of the four still leave a way to make a release object public."""
    block = hcl_block(MAIN, 'resource "aws_s3_bucket_public_access_block" "releases"')

    for flag in (
        "block_public_acls",
        "block_public_policy",
        "ignore_public_acls",
        "restrict_public_buckets",
    ):
        assert attribute(block, flag) == "true", flag


# --- The deployment role ------------------------------------------------------


def test_deploy_role_is_limited_to_s3_ssm_and_target_health() -> None:
    policy = hcl_block(MAIN, 'data "aws_iam_policy_document" "github_deploy"')
    assert statement_with_action(policy, "s3:PutObject")
    assert statement_with_action(policy, "ssm:SendCommand")
    assert statement_with_action(policy, "ssm:GetCommandInvocation")
    assert statement_with_action(policy, "elasticloadbalancing:DescribeTargetHealth")
    for forbidden in ("ecr:", "apprunner:", "iam:PassRole"):
        assert forbidden not in policy


def test_send_command_names_the_one_document_and_the_one_instance() -> None:
    """`ssm:SendCommand` on `"*"` is remote root on every instance in the
    account, so this is the statement that has to stay scoped."""
    policy = hcl_block(MAIN, 'data "aws_iam_policy_document" "github_deploy"')
    statement = statement_with_action(policy, "ssm:SendCommand")

    assert "aws_ssm_document.deploy.arn" in statement
    assert "aws_instance.docs.arn" in statement
    assert '"*"' not in statement


def test_the_deploy_role_is_bound_to_one_branch_of_one_repository() -> None:
    trust = hcl_block(MAIN, 'data "aws_iam_policy_document" "github_deploy_assume_role"')

    assert "token.actions.githubusercontent.com:sub" in trust
    assert "local.github_repository_subject" in trust

    # Reading the operators rather than searching for the absent one, so that
    # naming `StringLike` in a comment cannot satisfy or break this.
    assert re.findall(r'test\s*=\s*"([^"]+)"', trust) == ["StringEquals", "StringEquals"]


# --- Host bootstrap -----------------------------------------------------------


def test_bootstrap_creates_a_non_root_service_and_root_only_session_key() -> None:
    assert "User=authifi-docs" in USER_DATA
    assert "Group=authifi-docs" in USER_DATA
    assert "chmod 0600 /etc/authifi-docs/session.env" in USER_DATA
    assert "openssl rand -hex 32" in USER_DATA
    assert "SESSION_SECRET=" not in MAIN


def test_the_session_secret_survives_a_reboot() -> None:
    """Regenerating it on every boot would sign every existing cookie out, and
    a released instance reboots for reasons that have nothing to do with deploys.
    """
    assert "if [[ ! -s /etc/authifi-docs/session.env ]]; then" in USER_DATA


def test_the_installer_is_delivered_to_the_host_without_shell_interpolation() -> None:
    """The installer is a bash script full of `$`, `${}`, and heredocs. Pasting
    it into a `templatefile` body would have Terraform try to interpolate it;
    base64 keeps the two languages apart.
    """
    assert "${deploy_script_base64}" in USER_DATA
    assert 'base64encode(file("${path.module}/scripts/deploy-release.sh"))' in MAIN


def test_the_example_variables_carry_no_secret_material() -> None:
    lowered = TFVARS_EXAMPLE.lower()

    for forbidden in (
        "secretsmanager",
        "client_secret",
        "session_secret",
        "image_identifier",
        "ecr_",
        "apprunner",
    ):
        assert forbidden not in lowered, forbidden
