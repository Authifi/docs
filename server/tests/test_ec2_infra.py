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

from server.tests.hcl_support import (
    actions,
    hcl_block,
    hcl_list,
    nested_blocks,
    resource_bodies,
    statement_with_action,
    strip_comments,
)

ROOT = Path(__file__).resolve().parents[2]
MAIN = (ROOT / "infra" / "main.tf").read_text(encoding="utf-8")
VARIABLES = (ROOT / "infra" / "variables.tf").read_text(encoding="utf-8")
OUTPUTS = (ROOT / "infra" / "outputs.tf").read_text(encoding="utf-8")
USER_DATA = (ROOT / "infra" / "templates" / "user-data.sh.tftpl").read_text(encoding="utf-8")
TFVARS_EXAMPLE = (ROOT / "infra" / "terraform.tfvars.example").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
INFRA_README = (ROOT / "infra" / "README.md").read_text(encoding="utf-8")
OPERATIONS_DOC = (ROOT / "docs" / "operations" / "aws-oidc-hosting.md").read_text(encoding="utf-8")


def attribute(body: str, name: str) -> str | None:
    """One top-level `name = value` from a block body, whitespace normalised.

    The exact-text assertions below pin `terraform fmt` alignment where the
    alignment is itself worth pinning. Everywhere else, reading the value is
    what makes an assertion about behaviour rather than about formatting.
    """
    match = re.search(rf"^\s*{re.escape(name)}\s*=\s*(.+?)\s*$", body, re.MULTILINE)
    return match[1] if match else None


def security_group_rules(direction: str) -> dict[str, str]:
    return resource_bodies(MAIN, f"aws_vpc_security_group_{direction}_rule")


def app_rules(direction: str) -> dict[str, str]:
    """Every rule attached to the app security group, by resource name."""
    return {
        name: body
        for name, body in security_group_rules(direction).items()
        if attribute(body, "security_group_id") == "aws_security_group.app.id"
    }


def instance_preconditions() -> list[str]:
    return nested_blocks(hcl_block(MAIN, 'resource "aws_instance" "docs"'), "precondition")


def user_data_statements() -> str:
    """User data with its whole-line shell comments removed.

    Same reason `strip_comments` exists for the HCL: a comment explaining why
    a construct is used otherwise counts as a use of it.
    """
    return re.sub(r"(?m)^[ \t]*#.*$", "", USER_DATA)


def ssm_install_script() -> str:
    """The shell the deployment document runs, as the agent will assemble it.

    The agent joins `runCommand` entries with newlines into one script file, so
    reading them joined is reading the script.
    """
    document = hcl_block(MAIN, 'resource "aws_ssm_document" "deploy"')
    return "\n".join(hcl_list(document, "runCommand"))


def strip_historical_migration_sections(markdown: str) -> str:
    """Drop explicitly historical migration sections from Markdown.

    The operator docs may keep a narrowly scoped historical section for
    Cloudflare cutover or rollback context. That section must not excuse stale
    production claims elsewhere in the current runbook.
    """
    kept: list[str] = []
    skipping_level: int | None = None

    for line in markdown.splitlines():
        heading = re.match(r"^(#{2,6})\s+(.*)$", line)
        if heading:
            level = len(heading[1])
            title = heading[2].strip().lower()
            if skipping_level is not None and level <= skipping_level:
                skipping_level = None
            if skipping_level is None and ("historical" in title or "migration" in title):
                skipping_level = level
                continue
        if skipping_level is None:
            kept.append(line)

    return "\n".join(kept)


def operator_docs_text() -> str:
    return "\n".join(
        strip_historical_migration_sections(text).lower()
        for text in (README, INFRA_README, OPERATIONS_DOC)
    )


# --- The App Runner architecture is gone, not merely unused ------------------


def test_app_runner_and_ecr_are_absent() -> None:
    assert 'resource "aws_apprunner_' not in MAIN
    assert 'resource "aws_ecr_' not in MAIN
    assert "apprunner" not in VARIABLES.lower()
    assert "ecr_" not in VARIABLES.lower()


# --- The instance is private ---------------------------------------------------


def test_instance_is_private_and_uses_encrypted_ebs() -> None:
    instance = hcl_block(MAIN, 'resource "aws_instance" "docs"')

    assert attribute(instance, "associate_public_ip_address") == "false"
    assert attribute(instance, "subnet_id") == "var.private_app_subnet_id"
    assert attribute(hcl_block(instance, "root_block_device"), "encrypted") == "true"


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
    assert attribute(alb, "subnets") == "var.public_subnet_ids"


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
    """Enumerated, not spot-checked. `app_from_alb` being exactly right says
    nothing about a second ingress rule opening 22 to the world beside it, and
    that rule is the whole difference between a private host and a public one.
    """
    ingress = app_rules("ingress")

    assert set(ingress) == {"app_from_alb"}

    rule = ingress["app_from_alb"]
    assert attribute(rule, "referenced_security_group_id") == "aws_security_group.alb.id"
    assert attribute(rule, "from_port") == "var.app_port"
    assert attribute(rule, "to_port") == "var.app_port"

    # Any of these would admit something other than the load balancer.
    for opener in ("cidr_ipv4", "cidr_ipv6", "prefix_list_id"):
        assert attribute(rule, opener) is None, opener


def test_no_security_group_rule_anywhere_opens_ssh() -> None:
    every_rule = {**security_group_rules("ingress"), **security_group_rules("egress")}
    ports = {attribute(body, "from_port") for body in every_rule.values()}

    assert "22" not in ports


def test_security_groups_declare_no_inline_rules() -> None:
    """An inline `ingress` block is invisible to the enumeration above, so a
    port opened there would pass every rule assertion in this file."""
    for name in ("alb", "app"):
        group = strip_comments(hcl_block(MAIN, f'resource "aws_security_group" "{name}"'))

        assert not nested_blocks(group, "ingress"), name
        assert not nested_blocks(group, "egress"), name


def test_the_instance_has_no_ssh_key_pair() -> None:
    """Systems Manager is the only way onto this host. A key pair would put a
    credential outside that path, usable by anyone holding the private half and
    leaving none of the audit trail Session Manager does.
    """
    instance = hcl_block(MAIN, 'resource "aws_instance" "docs"')

    assert attribute(instance, "key_name") is None
    assert "key_name" not in strip_comments(instance)
    assert 'resource "aws_key_pair"' not in MAIN


def test_app_egress_supports_shared_nat_oidc_and_bootstrap() -> None:
    """The docs server performs OIDC discovery and the authorization-code
    exchange itself, and the host installs one Ubuntu package at first boot.
    Both leave the subnet through the shared NAT, so the security group has to
    permit them; VPC endpoints no longer stand in for that egress.
    """
    for name, port in (("app_http", 80), ("app_https", 443)):
        rule = app_rules("egress")[name]

        assert attribute(rule, "from_port") == str(port)
        assert attribute(rule, "to_port") == str(port)
        assert attribute(rule, "cidr_ipv4") == '"0.0.0.0/0"'

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
    app_egress = app_rules("egress")

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
    redirect = hcl_block(MAIN, 'resource "aws_lb_listener" "http"')
    health = hcl_block(hcl_block(MAIN, 'resource "aws_lb_target_group" "docs"'), "health_check")

    assert attribute(redirect, "status_code") == '"HTTP_301"'
    assert attribute(health, "path") == '"/health"'
    assert attribute(health, "matcher") == '"200"' 


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


def test_the_redirect_never_points_at_a_listener_that_does_not_exist_yet() -> None:
    """On the apply that flips to HTTPS, port 80 stops answering and starts
    redirecting to 443. Terraform is otherwise free to make that change before
    creating the listener on 443, leaving a window in which every request is
    redirected to a closed port — worse than the holding response it replaced.
    """
    redirect = hcl_block(MAIN, 'resource "aws_lb_listener" "http"')

    assert attribute(redirect, "depends_on") == "[aws_lb_listener.https]"


def test_the_advertised_origin_matches_the_name_the_certificate_covers() -> None:
    """The host advertises `public_base_url` as its OIDC redirect origin and the
    load balancer serves only `custom_domain_name`. A mismatch is a sign-in loop
    whose first visible symptom is Authifi rejecting the redirect URI, so it is
    worth catching in a plan rather than in production.
    """
    precondition = next(
        block
        for block in nested_blocks(hcl_block(MAIN, 'resource "aws_acm_certificate" "docs"'), "precondition")
        if "public_base_url" in block
    )
    condition = attribute(precondition, "condition") or ""

    assert "var.public_base_url" in condition
    assert "== var.custom_domain_name" in condition


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

    # Order, not just presence: the installer exits non-zero when either object
    # is missing, so staging after the run step would fail every deploy.
    assert re.findall(r'action = "(aws:[A-Za-z]+)"', document) == [
        "aws:downloadContent",
        "aws:downloadContent",
        "aws:runShellScript",
    ]


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


def test_the_instance_role_carries_exactly_the_two_expected_policies() -> None:
    """Enumerated and pinned, because an `AmazonS3FullAccess` attachment sits
    perfectly happily beside a correctly scoped `instance_releases` one, and
    every assertion that reads only the expected attachments still passes.

    This role is assumable by anything running on the host, so its total grant
    is what an application compromise gets.
    """
    attachments = {
        name: body
        for name, body in resource_bodies(MAIN, "aws_iam_role_policy_attachment").items()
        if attribute(body, "role") == "aws_iam_role.instance.name"
    }

    assert set(attachments) == {"instance_ssm_core", "instance_releases"}
    assert attribute(attachments["instance_ssm_core"], "policy_arn") == (
        '"arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"'
    )
    assert attribute(attachments["instance_releases"], "policy_arn") == (
        "aws_iam_policy.instance_releases.arn"
    )

    # Three ways to grant a role something without an attachment resource.
    assert 'resource "aws_iam_role_policy"' not in MAIN
    role = strip_comments(hcl_block(MAIN, 'resource "aws_iam_role" "instance"'))
    assert not nested_blocks(role, "inline_policy")
    assert attribute(role, "managed_policy_arns") is None


def test_no_customer_managed_policy_exists_beyond_the_two_expected() -> None:
    """A second policy is only harmless while nothing attaches it, and the
    attachment is one line away."""
    assert set(resource_bodies(MAIN, "aws_iam_policy")) == {"instance_releases", "github_deploy"}


def test_the_instance_role_reads_only_the_release_prefix() -> None:
    policy = hcl_block(MAIN, 'data "aws_iam_policy_document" "instance_releases"')
    statement = statement_with_action(policy, "s3:GetObject")

    # The whole grant, read from the actions lists rather than searched for.
    assert actions(policy) == ["s3:GetObject"]
    assert attribute(statement, "resources") == '["${aws_s3_bucket.releases.arn}/releases/*"]'


def test_the_private_subnet_is_checked_for_a_route_to_the_shared_nat() -> None:
    """`map_public_ip_on_launch == false` proves the subnet is not public. It
    does not prove there is a way out, and a subnet with no default route at all
    passes it — the symptom being an instance whose sign-in and whose Systems
    Manager registration both silently never work.

    Now that this root does not own the route table, reading it is the only way
    to know the shared NAT route is really there.
    """
    lookup = hcl_block(MAIN, 'data "aws_route_table" "app"')
    assert attribute(lookup, "subnet_id") == "var.private_app_subnet_id"

    precondition = next(
        block for block in instance_preconditions() if "aws_route_table" in block
    )
    condition = attribute(precondition, "condition") or ""

    # Both halves, so the check is not vacuous: a default route to an internet
    # gateway has no nat_gateway_id, and a NAT route for some narrower prefix
    # is not a default route. Either alone would pass a one-sided condition.
    assert "data.aws_route_table.app.routes" in condition
    assert 'route.cidr_block == "0.0.0.0/0"' in condition
    assert 'route.nat_gateway_id != ""' in condition
    assert condition.startswith("anytrue(")

    message = attribute(precondition, "error_message") or ""
    assert "private_app_subnet_id" in message
    assert "NAT" in message


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
    # The whole grant, enumerated. Searching for the absent strings read the
    # comments beside the statements as well as the statements.
    assert actions(policy) == [
        "s3:GetObject",
        "s3:PutObject",
        "ssm:SendCommand",
        "ssm:GetCommandInvocation",
        "ssm:ListCommandInvocations",
        "elasticloadbalancing:DescribeTargetHealth",
    ]


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


def test_bootstrap_installs_the_venv_package_ubuntu_leaves_out() -> None:
    """The Noble cloud image ships python3 3.12 with no `ensurepip`: that module
    is owned by python3.12-venv, which the image does not include. Without this
    the installer's `python3 -m venv` fails on the very first deploy.

    It has to happen before the service is enabled, and it is only reachable at
    all because the private subnet routes through the shared NAT.
    """
    bootstrap = user_data_statements()

    assert "apt-get update" in bootstrap
    assert re.search(r"apt-get install --yes .*\bpython3-venv\b", bootstrap)
    assert bootstrap.index("apt-get install") < bootstrap.index("systemctl enable authifi-docs")


def test_the_host_is_pointed_at_the_only_time_source_it_can_reach() -> None:
    """Egress permits UDP 123 to 169.254.169.123 and nowhere else, and Noble
    does not reliably use it. Ubuntu's chrony ships the ntp.ubuntu.com NTS
    pools, which need UDP 123 and TCP 4460 to the internet, and AWS's own Ubuntu
    instructions are to add the link-local server by hand.

    Unconfigured, the clock drifts behind a security group that silently drops
    every NTP packet, and on this host drift is an OIDC failure: `iat` and `exp`
    are the first things it breaks.
    """
    rule = app_rules("egress")["app_time_sync"]

    assert attribute(rule, "cidr_ipv4") == '"169.254.169.123/32"'
    assert attribute(rule, "ip_protocol") == '"udp"'

    bootstrap = user_data_statements()

    assert re.search(r"apt-get install --yes .*\bchrony\b", bootstrap)
    assert "server 169.254.169.123 prefer iburst" in bootstrap
    assert "systemctl restart chrony" in bootstrap

    # The distro pools are unreachable through that rule, so both layouts
    # Ubuntu has shipped them in are neutralised rather than left to time out.
    assert ": > /etc/chrony/sources.d/ubuntu-ntp-pools.sources" in bootstrap
    assert r"ubuntu\.com" in bootstrap


def test_the_bootstrap_package_install_does_not_fail_on_one_bad_response() -> None:
    """It is the first thing on this host to use the shared NAT, and `set -e`
    would turn a transient apt failure into an instance with no service unit and
    no installer.
    """
    bootstrap = user_data_statements()

    assert "DEBIAN_FRONTEND=noninteractive" in bootstrap

    # Pinned to the real count: `for attempt in 1; do` matched a loop that
    # retries nothing, and the terminal branch is what keeps a permanent
    # failure from looking like a successful boot.
    attempts = re.search(r"for attempt in ([\d ]+); do", bootstrap)
    assert attempts is not None
    assert attempts[1].split() == ["1", "2", "3", "4", "5"]

    # And the last attempt gives up rather than looping or continuing.
    assert 'if [[ "$attempt" -eq 5 ]]; then' in bootstrap
    assert re.search(r"-eq 5 \]\]; then\n.*\n\s*exit 1", bootstrap)


SECRET_MATERIAL = re.compile(r"session[_ -]?secret|client[_ -]?secret", re.IGNORECASE)

TEMPLATE_INPUTS = 'templatefile("${path.module}/templates/user-data.sh.tftpl",'


def test_no_terraform_variable_output_or_template_input_carries_a_secret() -> None:
    """`"SESSION_SECRET=" not in MAIN` was the entire guard, and it holds for a
    `variable "session_secret"` piped through `templatefile`, for an
    `output "session_secret"`, and for a `locals` entry holding one — every way
    the value could actually end up in Terraform state or in a plan file.

    Comments are stripped first, so the guard reads configuration rather than
    the prose explaining why the value is absent.
    """
    for filename, source in (("main.tf", MAIN), ("variables.tf", VARIABLES), ("outputs.tf", OUTPUTS)):
        found = SECRET_MATERIAL.search(strip_comments(source))

        assert found is None, f"{filename} names secret material: {found and found[0]}"


def test_the_template_receives_only_non_secret_configuration() -> None:
    """The template's inputs are the one channel from Terraform onto the host,
    so they are enumerated rather than searched: a name that does not read as a
    secret can still carry one.
    """
    inputs = strip_comments(hcl_block(MAIN, TEMPLATE_INPUTS))

    assert set(re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", inputs, re.MULTILINE)) == {
        "oidc_issuer",
        "oidc_client_id",
        "public_base_url",
        "site_dir",
        "post_logout_path",
        "app_port",
    }


def test_the_session_secret_is_generated_on_the_host_and_kept_root_only() -> None:
    """Terraform never sees it, so the only thing that may assign it is the
    host's own CSPRNG, and the file it lands in must never be readable by the
    service user: systemd reads `EnvironmentFile` as root before dropping
    privileges, so 0600 root-owned is sufficient and necessary.
    """
    assert re.search(
        r"printf 'SESSION_SECRET=%s\\n' \"\$\(openssl rand -hex 32\)\"", USER_DATA
    ), "the secret must come from openssl rand"

    # One writer, so there is no second path that could set a fixed value.
    assert user_data_statements().count("SESSION_SECRET") == 1

    # `umask` alone would leave the mode depending on when the file was made.
    assert re.search(r"\(\s*umask 077", USER_DATA)
    assert "chmod 0600 /etc/authifi-docs/session.env" in USER_DATA


def test_the_umask_for_the_session_secret_does_not_leak_into_the_rest() -> None:
    """A bare `umask 077` stays in effect for every file created after it, so
    the mode of anything written later would depend on statement order."""
    subshell = re.search(r"\(\s*\n\s*umask 077\n(.*?)\n\s*\)\n", USER_DATA, re.DOTALL)

    assert subshell is not None
    assert "session.env" in subshell[1]

    # One `umask` statement, so there is no second unscoped one to reason about.
    assert user_data_statements().count("umask") == 1


def test_the_session_secret_survives_a_reboot() -> None:
    """Regenerating it on every boot would sign every existing cookie out, and
    a released instance reboots for reasons that have nothing to do with deploys.
    """
    assert "if [[ ! -s /etc/authifi-docs/session.env ]]; then" in USER_DATA


def test_user_data_no_longer_carries_the_installer() -> None:
    """`user_data_replace_on_change = true` makes everything in user data part
    of the instance's identity, so while the installer lived here every edit to
    `deploy-release.sh` destroyed and rebuilt the host — which regenerates the
    session secret, signs every user out, empties the release tree, and needs a
    redeploy before the site answers again.
    """
    bootstrap = user_data_statements()

    assert "deploy_script_base64" not in bootstrap
    assert "authifi-docs-deploy" not in bootstrap
    assert "base64" not in bootstrap
    assert "deploy_script_base64" not in strip_comments(hcl_block(MAIN, TEMPLATE_INPUTS))


def test_the_document_delivers_the_installer_with_the_command_that_runs_it() -> None:
    """Carried by the document instead of the host, so an installer edit is a
    new document version rather than a replaced instance.

    The provider handles the promotion: on a content change it calls
    UpdateDocument and then UpdateDocumentDefaultVersion with the version that
    call returned, so `SendCommand` naming no version still resolves to the
    installer that was just applied.
    """
    document = hcl_block(MAIN, 'resource "aws_ssm_document" "deploy"')
    script = ssm_install_script()

    assert "local.deploy_script_base64" in document
    assert 'base64encode(file("${path.module}/scripts/deploy-release.sh"))' in MAIN

    # Only schema 2.0 and later can be updated in place; a 1.x document would
    # have to be destroyed and recreated on every installer edit.
    assert 'schemaVersion = "2.2"' in document
    assert "base64 -d" in script


def test_the_delivered_installer_is_root_only_and_does_not_outlive_the_command() -> None:
    """It is written to disk with the credentials of the SSM agent, which is
    root, and it is the thing that installs the site. A world-readable or
    lingering copy in a shared directory is a local privilege-escalation
    foothold on the next deploy.
    """
    script = ssm_install_script()

    # /run is root-owned tmpfs, unlike /tmp, which is world-writable.
    assert "install -d -m 0700 -o root -g root /run/authifi-docs" in script
    assert "mktemp /run/authifi-docs/" in script
    assert 'chmod 0700 "$installer"' in script
    assert re.search(r"trap 'rm -f \"\$installer\"' EXIT", script)


def test_the_installer_wrapper_stays_within_posix_shell() -> None:
    """The agent hardcodes `sh` and passes the assembled script to it, so on
    Ubuntu this runs under dash and the shebang is never consulted. `pipefail`,
    `[[`, and here-strings are all syntax errors there, and the failure would
    arrive as a broken deploy rather than as a broken plan.
    """
    script = ssm_install_script()

    assert script.startswith("set -eu\n")
    for bashism in ("pipefail", "[[", "<<<", "function "):
        assert bashism not in script, bashism


def test_the_release_archive_still_carries_its_own_copy_of_the_installer() -> None:
    """Provenance: the archive records which installer its release was built
    against, independently of whichever document version ran."""
    build = (ROOT / "scripts" / "build-release.sh").read_text(encoding="utf-8")

    assert "deploy/deploy-release.sh" in build


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


# --- Operator documentation must match the EC2 architecture -------------------


def test_operator_docs_name_the_native_ec2_architecture_only() -> None:
    text = operator_docs_text()

    assert "application load balancer" in text
    assert "private ec2" in text
    assert "systemd" in text
    assert "release archive" in text
    assert "journalctl -u authifi-docs" in text
    for forbidden in ("app runner", "awsapprunner", "ghcr", "self-hosted"):
        assert not re.search(rf"\b{re.escape(forbidden)}\b", text), forbidden
    assert not re.search(r"\becr\b", text), "operator docs still name production ECR"


def test_operator_docs_do_not_send_first_rollout_probes_to_the_old_origin() -> None:
    text = operator_docs_text()

    assert "certificate_validation_records" in text
    assert "wait for the certificate to become `issued`" in text
    assert "docs_alb_dns_name" in text
    assert "without moving `docs.authifi.io` yet" in text
    assert "workflow_dispatch" in text
    assert "connect directly to the alb" in text
    assert "cut dns from cloudflare" in text
    assert "rerun canonical verification" in text


def test_docs_describe_public_pkce_registration_without_a_secret() -> None:
    text = OPERATIONS_DOC

    assert "public client" in text
    assert "PKCE S256" in text
    assert "token_endpoint_auth_method=none" in text
