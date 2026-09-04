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
    assert "subnet_id                   = var.private_app_subnet_id" in instance
    root = hcl_block(instance, "root_block_device")
    assert "encrypted = true" in root


# --- The shared network is consumed, not rebuilt -------------------------------


def test_terraform_reuses_the_shared_vpc_and_subnets() -> None:
    """A second VPC would need its own NAT to give the server the internet
    egress the OIDC code exchange requires. This root takes Authifi's."""
    for resource in (
        'resource "aws_vpc"',
        'resource "aws_subnet"',
        'resource "aws_internet_gateway"',
        'resource "aws_nat_gateway"',
        'resource "aws_route"',
    ):
        assert resource not in MAIN, resource

    alb = hcl_block(MAIN, 'resource "aws_lb" "docs"')
    assert "subnets            = var.public_subnet_ids" in alb


def test_the_target_group_lives_in_the_shared_vpc() -> None:
    target = hcl_block(MAIN, 'resource "aws_lb_target_group" "docs"')

    assert attribute(target, "vpc_id") == "var.vpc_id"


def test_supplied_subnets_are_checked_against_the_supplied_vpc() -> None:
    """Three IDs that each exist but do not belong together fail at apply time
    with an AWS error about the load balancer, not about the inputs. The
    postconditions turn that into a plan-time message naming the variable.
    """
    for name in ("public", "app"):
        postcondition = hcl_block(hcl_block(MAIN, f'data "aws_subnet" "{name}"'), "postcondition")

        assert attribute(postcondition, "condition") == "self.vpc_id == var.vpc_id"
        assert "error_message" in postcondition


def test_the_load_balancer_checks_it_spans_two_zones() -> None:
    """Two distinct subnet IDs are not two availability zones, and an
    internet-facing load balancer sitting in one has no second zone.

    This is also what makes the subnet lookups load-bearing: referencing them
    here puts them ahead of the load balancer in the graph, so their vpc_id
    postconditions are what fails rather than an opaque AWS error.
    """
    precondition = hcl_block(hcl_block(MAIN, 'resource "aws_lb" "docs"'), "precondition")

    assert "data.aws_subnet.public" in precondition
    assert "availability_zone" in precondition
    assert "== 2" in precondition


def test_the_instance_rejects_a_subnet_that_hands_out_public_addresses() -> None:
    """`private_app_subnet_id` is a plain string, and a public subnet ID is a
    valid one. Catching it here fails the plan instead of quietly putting the
    docs server in a subnet with a route to an internet gateway.
    """
    precondition = hcl_block(hcl_block(MAIN, 'resource "aws_instance" "docs"'), "precondition")

    assert "data.aws_subnet.app.map_public_ip_on_launch == false" in precondition


def test_two_distinct_public_subnets_are_required() -> None:
    """An internet-facing ALB needs two availability zones, and the same subnet
    listed twice satisfies a length check while providing one."""
    block = hcl_block(VARIABLES, 'variable "public_subnet_ids"')

    assert "length(var.public_subnet_ids) == 2" in block
    assert "length(distinct(var.public_subnet_ids)) == 2" in block


def test_the_instance_requires_imdsv2() -> None:
    """IMDSv1 lets any SSRF in the docs server read the instance role's
    credentials with a single unauthenticated GET."""
    metadata = hcl_block(hcl_block(MAIN, 'resource "aws_instance" "docs"'), "metadata_options")

    assert attribute(metadata, "http_tokens") == '"required"'
    assert attribute(metadata, "http_put_response_hop_limit") == "1"


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


def test_app_egress_supports_shared_nat_oidc_and_bootstrap() -> None:
    """The docs server performs OIDC discovery and the authorization-code
    exchange itself, and the host installs one Ubuntu package at first boot.
    Both leave the subnet through the shared NAT, so the security group has to
    permit them; VPC endpoints no longer stand in for that egress.
    """
    for name, port in (("app_http", 80), ("app_https", 443)):
        rule = hcl_block(
            MAIN,
            f'resource "aws_vpc_security_group_egress_rule" "{name}"',
        )
        assert f"from_port   = {port}" in rule
        assert f"to_port     = {port}" in rule
        assert 'cidr_ipv4   = "0.0.0.0/0"' in rule

    assert 'resource "aws_vpc_endpoint"' not in MAIN


def test_the_app_resolves_names_inside_the_vpc_only() -> None:
    """DNS is the one flow that must not go to the internet: pointing it at an
    external resolver would break the VPC's own private zones."""
    for name, protocol in (("app_dns_udp", "udp"), ("app_dns_tcp", "tcp")):
        rule = hcl_block(MAIN, f'resource "aws_vpc_security_group_egress_rule" "{name}"')

        assert attribute(rule, "cidr_ipv4") == "data.aws_vpc.shared.cidr_block"
        assert attribute(rule, "ip_protocol") == f'"{protocol}"'
        assert attribute(rule, "from_port") == "53"


def test_app_egress_is_confined_to_named_ports() -> None:
    """`0.0.0.0/0` on 80 and 443 is required; `0.0.0.0/0` on every port is not.
    A blanket `ip_protocol = "-1"` rule would satisfy the flows above while
    opening every outbound port on the instance.
    """
    app_egress = {
        name: body
        for name, body in security_group_rules("egress").items()
        if attribute(body, "security_group_id") == "aws_security_group.app.id"
    }

    assert set(app_egress) == {
        "app_dns_tcp",
        "app_dns_udp",
        "app_http",
        "app_https",
        "app_time_sync",
    }
    for name, body in app_egress.items():
        assert attribute(body, "ip_protocol") in ('"tcp"', '"udp"'), name
        assert attribute(body, "from_port") == attribute(body, "to_port"), name


# --- The public edge ----------------------------------------------------------


def test_alb_redirects_http_and_checks_application_health() -> None:
    http = hcl_block(MAIN, 'resource "aws_lb_listener" "http"')
    target = hcl_block(MAIN, 'resource "aws_lb_target_group" "docs"')
    assert 'status_code = "HTTP_301"' in http
    assert 'path                = "/health"' in hcl_block(target, "health_check")


def test_one_stable_listener_owns_port_eighty() -> None:
    """Two count-gated listeners on port 80 are a create racing a destroy, and
    AWS rejects the overlap with DuplicateListener, so the flip to HTTPS needed
    a second apply to converge.

    One unconditional listener whose default action swaps between the bootstrap
    response and the redirect is an in-place update, so the flip lands in one
    apply.
    """
    http = hcl_block(MAIN, 'resource "aws_lb_listener" "http"')

    assert 'resource "aws_lb_listener" "bootstrap"' not in MAIN
    assert attribute(http, "count") is None
    assert attribute(http, "port") == "80"

    # Exact inverses, so port 80 always has one default action and never two.
    assert re.findall(r"for_each\s*=\s*(.+)", http) == [
        "var.enable_https_listener ? [1] : []",
        "var.enable_https_listener ? [] : [1]",
    ]
    assert '"redirect"' in http
    assert '"fixed-response"' in http


def test_https_is_served_only_once_the_certificate_is_enabled() -> None:
    """Nothing else claims port 443, so this one stays count-gated: it cannot
    exist at all until the certificate it references has been issued.
    """
    https = hcl_block(MAIN, 'resource "aws_lb_listener" "https"')

    assert attribute(https, "count") == "var.enable_https_listener ? 1 : 0"
    assert attribute(https, "certificate_arn") == "aws_acm_certificate.docs.arn"


def test_the_certificate_is_not_blocked_on_validation_at_apply_time() -> None:
    """`aws_acm_certificate_validation` waits for records this root cannot
    create, so its presence would deadlock the very first apply."""
    assert 'resource "aws_acm_certificate_validation"' not in MAIN
    assert 'output "certificate_validation_records"' in OUTPUTS


# --- Deployment reaches the instance without SSH or a public address ---------


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


def test_bootstrap_installs_the_venv_package_ubuntu_leaves_out() -> None:
    """The Noble cloud image ships python3 3.12 with no `ensurepip`: that module
    is owned by python3.12-venv, which the image does not include. Without this
    the installer's `python3 -m venv` fails on the very first deploy.

    It has to happen before the service is enabled, and it is only reachable at
    all because the private subnet routes through the shared NAT.
    """
    assert "apt-get update" in USER_DATA
    assert "apt-get install --yes python3-venv" in USER_DATA
    assert USER_DATA.index("apt-get install") < USER_DATA.index("systemctl enable")


def test_the_bootstrap_package_install_does_not_fail_on_one_bad_response() -> None:
    """It is the first thing on this host to use the shared NAT, and `set -e`
    would turn a transient apt failure into an instance with no service unit and
    no installer.
    """
    assert "DEBIAN_FRONTEND=noninteractive" in USER_DATA
    assert re.search(r"for attempt in .*; do", USER_DATA)


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
