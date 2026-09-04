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

import json
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import yaml

from server.app import normalize_origin, validate_oidc_issuer
from server.tests.hcl_support import (
    actions,
    attribute,
    hcl_block,
    hcl_list,
    nested_blocks,
    resource_bodies,
    statement_with_action,
    statements,
    strip_comments,
    variable_accepts,
    variable_conditions,
)

ROOT = Path(__file__).resolve().parents[2]
MAIN = (ROOT / "infra" / "main.tf").read_text(encoding="utf-8")
VARIABLES = (ROOT / "infra" / "variables.tf").read_text(encoding="utf-8")
OUTPUTS = (ROOT / "infra" / "outputs.tf").read_text(encoding="utf-8")
USER_DATA = (ROOT / "infra" / "templates" / "user-data.sh.tftpl").read_text(encoding="utf-8")
TFVARS_EXAMPLE = (ROOT / "infra" / "terraform.tfvars.example").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
INFRA_README = (ROOT / "infra" / "README.md").read_text(encoding="utf-8")
# Deliberately outside `docs/`: everything under that tree is built, indexed
# for search, and served to every identity the tenant accepts.
OPERATIONS_DOC = (ROOT / "operations" / "aws-oidc-hosting.md").read_text(encoding="utf-8")
DEPLOY_WORKFLOW = yaml.safe_load(
    (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
)
LOCALS = hcl_block(MAIN, "locals")

# A rule of the shape the enumeration below exists to catch, kept next to the
# real HCL so the assertion is exercised against something it must reject.
BLANKET_ALB_EGRESS = """
resource "aws_vpc_security_group_egress_rule" "alb_anywhere" {
  security_group_id = aws_security_group.alb.id
  description       = "blanket egress"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}
"""


def security_group_rules(direction: str, source: str = MAIN) -> dict[str, str]:
    return resource_bodies(source, f"aws_vpc_security_group_{direction}_rule")


def group_rules(group: str, direction: str, source: str = MAIN) -> dict[str, str]:
    """Every rule attached to one security group, by resource name."""
    return {
        name: body
        for name, body in security_group_rules(direction, source).items()
        if attribute(body, "security_group_id") == f"aws_security_group.{group}.id"
    }


def app_rules(direction: str) -> dict[str, str]:
    return group_rules("app", direction)


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


def variable_default(name: str) -> str:
    """One variable's committed default, unquoted."""
    default = attribute(hcl_block(VARIABLES, f'variable "{name}"'), "default")
    assert default is not None, f"variable {name} declares no default"
    return default.strip('"')


def resolve(expression: str) -> str:
    """A single-valued HCL string expression as the string it renders.

    Only the shapes the trust policy uses are resolved -- a quoted template, a
    `var.` reference, a `local.` reference -- because an expression this reader
    cannot evaluate is one it cannot honestly assert anything about.
    """
    expression = expression.strip()

    if expression.startswith('"'):
        assert expression.endswith('"'), expression
        return re.sub(
            r"\$\{([^}]+)\}",
            lambda match: resolve(match[1]),
            expression[1:-1],
        )
    if expression.startswith("var."):
        return variable_default(expression.removeprefix("var."))
    if expression.startswith("local."):
        name = expression.removeprefix("local.")
        value = attribute(LOCALS, name)
        assert value is not None, f"no local named {name}"
        return resolve(value)

    raise AssertionError(f"unresolvable expression {expression!r}")


def resolve_list(expression: str) -> list[str]:
    assert expression.startswith("[") and expression.endswith("]"), expression
    return [
        resolve(element)
        for element in expression[1:-1].split(",")
        if element.strip()
    ]


GITHUB_CLAIM = "token.actions.githubusercontent.com:"


def github_trust_conditions() -> dict[str, tuple[str, list[str]]]:
    """Every GitHub OIDC claim the deploy role's trust policy binds.

    Keyed by claim, valued by the operator and the resolved values. Read out of
    the `condition` blocks rather than searched for, so a claim named only in a
    comment neither satisfies nor breaks an assertion -- and a claim bound
    twice, which is how an exact match quietly becomes a widening, fails here.
    """
    trust = strip_comments(
        hcl_block(MAIN, 'data "aws_iam_policy_document" "github_deploy_assume_role"')
    )
    bound: dict[str, tuple[str, list[str]]] = {}

    for block in nested_blocks(trust, "condition"):
        variable = (attribute(block, "variable") or "").strip('"')
        assert variable.startswith(GITHUB_CLAIM), f"unexpected condition variable {variable!r}"

        claim = variable.removeprefix(GITHUB_CLAIM)
        assert claim not in bound, f"{claim} is bound by two conditions"

        operator = (attribute(block, "test") or "").strip('"')
        values = attribute(block, "values")
        assert values is not None, f"{claim} condition carries no values"
        bound[claim] = (operator, resolve_list(values))

    return bound


def deploy_workflow_triggers() -> dict:
    """The workflow's `on:` mapping.

    YAML 1.1 reads a bare `on` as the boolean `true`, which is why this cannot
    simply be `DEPLOY_WORKFLOW["on"]`.
    """
    triggers = DEPLOY_WORKFLOW.get("on", DEPLOY_WORKFLOW.get(True))
    assert isinstance(triggers, dict), triggers
    return triggers


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


def test_reading_an_argument_does_not_reach_into_a_nested_block() -> None:
    """Every assertion in this file names a block and one of its arguments. If
    the reader answered with a sub-block's argument instead, an assertion about
    the instance could be satisfied by its `root_block_device`, and one about a
    security group rule by an unrelated nested value.
    """
    instance = hcl_block(MAIN, 'resource "aws_instance" "docs"')

    assert attribute(instance, "encrypted") is None
    assert attribute(instance, "http_tokens") is None
    assert attribute(instance, "condition") is None
    assert attribute(hcl_block(instance, "metadata_options"), "http_tokens") == '"required"'


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


def test_each_public_subnet_route_table_is_read_from_the_subnet_id() -> None:
    """The module consumes shared subnets rather than owning them, so whether
    each one is actually public is an assumption that has to be read back."""
    lookup = hcl_block(MAIN, 'data "aws_route_table" "public"')

    assert attribute(lookup, "for_each") == "toset(var.public_subnet_ids)"
    assert attribute(lookup, "subnet_id") == "each.value"


def test_the_load_balancer_requires_an_internet_gateway_default_route() -> None:
    """Two private subnets in different zones satisfy every check this root
    used to make. An internet-facing load balancer in them is reachable only
    from inside the VPC, which is not what this deployment is for."""
    preconditions = nested_blocks(hcl_block(MAIN, 'resource "aws_lb" "docs"'), "precondition")
    route_precondition = next(
        block for block in preconditions if "aws_route_table.public" in block
    )

    assert "alltrue(" in route_precondition
    assert "data.aws_route_table.public" in route_precondition
    assert 'route.cidr_block == "0.0.0.0/0"' in route_precondition
    assert 'startswith(route.gateway_id, "igw-")' in route_precondition
    assert 'route.gateway_id != ""' not in route_precondition

    message = (attribute(route_precondition, "error_message") or "").lower()
    assert "public_subnet_ids" in message
    assert "internet gateway" in message or "igw" in message


def test_the_deployment_requires_the_standard_commercial_aws_partition() -> None:
    """Canonical's AMI owner and the GitHub OIDC audience both assume the
    commercial `aws` partition. A syntactically valid China or GovCloud
    region passes variable validation and then fails during apply with an
    opaque AMI or trust error."""
    preconditions = nested_blocks(hcl_block(MAIN, 'data "aws_ami" "ubuntu"'), "precondition")
    partition_precondition = next(
        block for block in preconditions if "aws_partition" in block
    )
    condition = attribute(partition_precondition, "condition") or ""

    assert condition == 'data.aws_partition.current.partition == "aws"'

    message = (attribute(partition_precondition, "error_message") or "").lower()
    assert "aws" in message
    assert "partition" in message or "commercial" in message


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


def assert_alb_egress_reaches_the_app_port_only(source: str) -> None:
    """The load balancer's egress, enumerated the way its ingress counterpart is.

    `alb_to_app` being exactly right says nothing about a second rule beside it
    sending the load balancer's security group anywhere else, and a blanket
    `ip_protocol = "-1"` egress rule is how that arrives.
    """
    egress = group_rules("alb", "egress", source)

    assert set(egress) == {"alb_to_app"}

    rule = egress["alb_to_app"]
    assert attribute(rule, "referenced_security_group_id") == "aws_security_group.app.id"
    assert attribute(rule, "ip_protocol") == '"tcp"'
    assert attribute(rule, "from_port") == "var.app_port"
    assert attribute(rule, "to_port") == "var.app_port"

    # Any of these would let the load balancer reach something else entirely.
    for opener in ("cidr_ipv4", "cidr_ipv6", "prefix_list_id"):
        assert attribute(rule, opener) is None, opener


def test_the_load_balancer_can_reach_the_app_port_and_nothing_else() -> None:
    assert_alb_egress_reaches_the_app_port_only(MAIN)


def test_the_alb_egress_check_rejects_a_blanket_extra_rule() -> None:
    """Proof that the enumeration above has teeth."""
    with pytest.raises(AssertionError):
        assert_alb_egress_reaches_the_app_port_only(MAIN + BLANKET_ALB_EGRESS)


def resolved_port(value: str | None) -> int:
    """A rule's port as a number, resolving `var.name` through its default.

    Failing on anything else is deliberate: a port this reader cannot evaluate
    is a port the SSH assertion below cannot honestly make a claim about.
    """
    assert value is not None, "rule declares no port"
    if re.fullmatch(r"\d+", value):
        return int(value)

    reference = re.fullmatch(r"var\.([A-Za-z0-9_]+)", value)
    assert reference, f"unresolvable port expression {value!r}"

    default = attribute(hcl_block(VARIABLES, f'variable "{reference[1]}"'), "default")
    assert default is not None and re.fullmatch(r"\d+", default), (
        f"variable {reference[1]} has no numeric default"
    )
    return int(default)


def assert_rule_does_not_open_ssh(name: str, body: str) -> None:
    """No rule may span port 22, whether or not it names it.

    Reading `from_port` alone missed both shapes that matter: a range such as
    `0`–`65535` contains 22 without mentioning it, and `ip_protocol = "-1"`
    opens every port while declaring none.
    """
    assert attribute(body, "ip_protocol") != '"-1"', f"{name} opens every protocol"

    low = resolved_port(attribute(body, "from_port"))
    high = resolved_port(attribute(body, "to_port"))

    assert not low <= 22 <= high, f"{name} opens port 22 through {low}-{high}"


def test_no_security_group_rule_anywhere_opens_ssh() -> None:
    every_rule = {**security_group_rules("ingress"), **security_group_rules("egress")}

    assert every_rule

    for name, body in every_rule.items():
        assert_rule_does_not_open_ssh(name, body)


def test_the_ssh_check_rejects_ranges_and_protocol_wildcards_that_reach_22() -> None:
    """Proof that the assertion above has teeth."""
    for body in (
        'ip_protocol = "tcp"\nfrom_port = 0\nto_port = 65535\n',
        'ip_protocol = "tcp"\nfrom_port = 22\nto_port = 22\n',
        'ip_protocol = "-1"\ncidr_ipv4 = "0.0.0.0/0"\n',
        'ip_protocol = "tcp"\ncidr_ipv4 = "0.0.0.0/0"\n',
    ):
        with pytest.raises(AssertionError):
            assert_rule_does_not_open_ssh("probe", body)


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
    """Read from the `redirect` block itself. The listener also carries a
    `fixed_response` with a `status_code` of its own, so a status code found
    anywhere under the listener says nothing about which action carries it.
    """
    listener = hcl_block(MAIN, 'resource "aws_lb_listener" "http"')
    redirects = nested_blocks(listener, "redirect")
    health = hcl_block(hcl_block(MAIN, 'resource "aws_lb_target_group" "docs"'), "health_check")

    assert len(redirects) == 1
    assert attribute(redirects[0], "port") == '"443"'
    assert attribute(redirects[0], "protocol") == '"HTTPS"'
    assert attribute(redirects[0], "status_code") == '"HTTP_301"'
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


def test_the_derived_release_bucket_name_is_a_name_s3_will_accept() -> None:
    """`service_name` names the load balancer *and* the release bucket, and the
    two have different alphabets.

    An ELB name may be mixed case, an S3 bucket name may not, and the bucket is
    derived from this variable by default. `Authifi-Docs` therefore passed the
    validation, planned cleanly, and failed during apply at bucket creation --
    after the VPC lookups, the security groups, and the certificate had already
    been created. One lowercase contract for the variable is what makes the
    derived name valid by construction, rather than a second rule that
    lowercases it and leaves the plan claiming a name nothing uses.
    """
    derived = attribute(LOCALS, "release_bucket_name") or ""

    assert "var.service_name" in derived
    assert "lower(" not in derived, "the variable is the contract, not a call site fix-up"

    for value in ("authifi-docs", "docs", "a1", "a" * 32):
        assert variable_accepts(VARIABLES, "service_name", value), value

    for value in (
        "Authifi-Docs",
        "AUTHIFI-DOCS",
        "authifi_docs",
        "authifi.docs",
        "-authifi-docs",
        "authifi-docs-",
        "",
        "a" * 33,
        "authifi docs",
    ):
        assert not variable_accepts(VARIABLES, "service_name", value), value


@pytest.mark.parametrize("value", ["authifi-docs", "docs", "a1", "a" * 32])
def test_every_accepted_service_name_derives_a_legal_bucket_name(value: str) -> None:
    """The derived name is `<service_name>-releases-<12-digit account id>`, and
    S3 judges the whole thing: 3 to 63 characters, lowercase letters, digits,
    hyphens and periods only, and no hyphen at either end.
    """
    bucket = f"{value}-releases-123456789012"

    assert 3 <= len(bucket) <= 63, bucket
    assert re.fullmatch(r"[a-z0-9][a-z0-9.-]*[a-z0-9]", bucket), bucket


# Prefixes each of the three consumers reserves. Lowercase alphanumerics and
# hyphens is necessary but not sufficient: every one of these is a name the
# character rule accepts and the service refuses, which means an apply that
# creates the VPC lookups, the security groups, and the certificate and then
# fails partway through.
RESERVED_SERVICE_NAMES = (
    # S3 refuses a bucket name starting with either of these outright.
    "xn--docs",
    "sthree-docs",
    "sthree-configurator-docs",
    # An internet-facing ALB name may not start with `internal-`, which is how
    # the API distinguishes the internal scheme.
    "internal-docs",
    # Systems Manager refuses a document name beginning with any of these, and
    # `local.ssm_document_name` derives one from this variable.
    "aws-docs",
    "awsdocs",
    "amazon-docs",
    "amazondocs",
    "amzn-docs",
    "amzndocs",
)

# Names that merely contain a reserved word, which is fine: only the prefix is
# reserved, and refusing these would rule out plausible service names.
SERVICE_NAMES_THAT_ONLY_LOOK_RESERVED = (
    "authifi-aws-docs",
    "docs-internal",
    "docs-xn--x",
    "my-amazon-docs",
    "sthre-docs",
    "internally-managed",
    "awful-docs",
)


@pytest.mark.parametrize("value", RESERVED_SERVICE_NAMES)
def test_a_service_name_a_reserved_prefix_would_break_is_refused(value: str) -> None:
    """The character rule alone let these through, and each one fails during
    apply rather than during plan.

    `xn--` is S3's punycode prefix and `sthree-` is reserved for its own use;
    `internal-` is how the ELB API spells the internal scheme, so an
    internet-facing load balancer may not be named with it; and Systems
    Manager reserves `aws`, `amazon`, and `amzn` for document names, which
    this variable derives one of.
    """
    assert not variable_accepts(VARIABLES, "service_name", value), value


@pytest.mark.parametrize("value", SERVICE_NAMES_THAT_ONLY_LOOK_RESERVED)
def test_a_service_name_that_merely_contains_a_reserved_word_still_works(
    value: str,
) -> None:
    """Only the prefix is reserved. A rule written with `contains` rather than
    an anchored one would refuse a perfectly good name, and the failure would
    look like a bug in the module rather than a decision."""
    assert variable_accepts(VARIABLES, "service_name", value), value


def test_the_docs_say_the_config_change_replaces_the_instance() -> None:
    """`config.json` replaced `environment` in user data, and user data is part
    of the instance's identity under `user_data_replace_on_change`.

    So applying this change destroys the running host and builds a new one,
    which has three consequences an operator has to know before running it: the
    new instance boots with no release under `current`, so the site is down
    until a deploy runs; the session secret is regenerated, so every live
    session is invalidated; and a deploy started while the replacement is in
    progress can install onto the instance that is about to be destroyed, or
    fail against an instance ID that no longer exists.

    An operator who reads none of that runs `terraform apply` on a Friday and
    discovers it from the outage.
    """
    for name, text in (
        ("infra/README.md", INFRA_README),
        ("operations/aws-oidc-hosting.md", OPERATIONS_DOC),
    ):
        lowered = text.lower()

        assert "config.json" in lowered, name

    section = migration_section()
    lowered = section.lower()

    assert "user_data_replace_on_change" in section
    # The outage, and that a deploy is what ends it.
    assert "down" in lowered
    assert "workflow_dispatch" in lowered
    # The instance ID changing, and the variable that has to follow it.
    assert "docs_instance_id" in lowered
    # Every session logged out.
    assert "session_secret" in lowered
    assert "invalidat" in lowered or "logged out" in lowered
    # And the ordering hazard: a deploy started against the outgoing host.
    assert "do not start a deploy" in lowered or "do not deploy" in lowered


def migration_section() -> str:
    """The `config.json` migration runbook, as its own section.

    Read as a section rather than as the whole file, so a phrase that happens
    to appear in an unrelated paragraph does not satisfy an assertion about
    this one.
    """
    match = re.search(
        r"^### Migrating an existing instance to `config\.json`$(.*?)(?=^## )",
        INFRA_README,
        re.MULTILINE | re.DOTALL,
    )

    assert match, "infra/README.md has no config.json migration section"
    return match[1]


def test_the_migration_runbook_is_ordered_and_ends_in_a_deploy() -> None:
    """The steps only work in one order: apply, re-read the instance ID, update
    the repository variable, then deploy. An operator who deploys before
    updating the variable sends the command to a terminated instance."""
    steps = re.findall(r"^\d+\. (.+)$", migration_section(), re.MULTILINE)

    assert len(steps) >= 4, steps

    ordered = " || ".join(steps).lower()

    assert ordered.index("plan") < ordered.index("apply")
    assert ordered.index("apply") < ordered.index("docs_instance_id")
    assert ordered.index("docs_instance_id") < ordered.index("workflow_dispatch")


def test_documented_commands_name_outputs_the_module_actually_declares() -> None:
    """A migration runbook is only followed once, so a stale output name in it
    is discovered under time pressure."""
    outputs = set(
        re.findall(
            r'^output "([^"]+)"',
            (ROOT / "infra" / "outputs.tf").read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    )

    assert outputs, "the module declares no outputs"

    for name, text in (
        ("infra/README.md", INFRA_README),
        ("operations/aws-oidc-hosting.md", OPERATIONS_DOC),
    ):
        referenced = set(re.findall(r"terraform -chdir=infra output (?:-raw )?(\w+)", text))

        assert referenced, name
        assert referenced <= outputs, f"{name} names {referenced - outputs}"


def test_the_reserved_prefixes_cover_the_three_consumers_the_name_reaches() -> None:
    """Read from the configuration rather than restated, so the rule is checked
    against the resources that actually take this name.

    Each of the three has its own reserved list, and the variable's validation
    is their union. A fourth consumer with a fourth list is one this test makes
    visible.
    """
    configuration = strip_comments(MAIN)

    assert "var.service_name" in (attribute(LOCALS, "release_bucket_name") or "")
    assert re.search(
        r'resource "aws_lb" "\w+"[\s\S]*?name\s*=\s*var\.service_name', configuration
    )
    assert re.search(
        r'resource "aws_ssm_document" "\w+"[\s\S]*?name\s*=\s*"\$\{var\.service_name\}',
        configuration,
    )

    condition = " ".join(variable_conditions(VARIABLES, "service_name"))
    for reserved in ("xn--", "sthree-", "internal-", "aws", "amazon", "amzn"):
        assert reserved in condition, reserved


# The two spellings of "the root of this origin", which mean the same
# deployment and both have to keep working.
PUBLIC_BASE_URLS_THAT_WORK = ("https://docs.authifi.io", "https://docs.authifi.io/")

# Everything the application could not honour if Terraform handed it over. The
# routes are all rooted at `/` and the load balancer strips no prefix, so a
# path here is a deployment whose callback URL, post-logout URL, and probe
# targets all name something nothing serves.
PUBLIC_BASE_URLS_THAT_CANNOT_WORK = (
    "http://docs.authifi.io",
    "https://docs.example.com",
    "https://staging.authifi.io",
    "https://docs.authifi.io/docs",
    "https://docs.authifi.io/docs/",
    "https://docs.authifi.io//",
    "https://docs.authifi.io/privacy-policy/",
    "https://docs.authifi.io?probe=1",
    "https://docs.authifi.io/?probe=1",
    "https://docs.authifi.io#fragment",
    "https://user:pass@docs.authifi.io",
    "https://docs.authifi.io:8443",
    "https://docs.authifi.io/docs?probe=1#fragment",
)

CUSTOM_DOMAIN_NAMES_THAT_WORK = ("docs.authifi.io",)

CUSTOM_DOMAIN_NAMES_THAT_CANNOT_WORK = (
    "docs.example.com",
    "staging.authifi.io",
    "authifi.io",
    "DOCS.authifi.io",
)

SITE_DIRS_THAT_WORK = ("/opt/authifi-docs/current/site",)

SITE_DIRS_THAT_CANNOT_WORK = (
    "/opt/authifi-docs/current/site/",
    "/opt/authifi-docs/site",
    "/opt/a docs/site",
    "/var/www/docs",
)


@pytest.mark.parametrize("value", PUBLIC_BASE_URLS_THAT_WORK)
def test_terraform_accepts_the_origin_this_deployment_actually_serves(value: str) -> None:
    assert variable_accepts(VARIABLES, "public_base_url", value)


@pytest.mark.parametrize("value", CUSTOM_DOMAIN_NAMES_THAT_WORK)
def test_terraform_accepts_the_one_hostname_this_site_serves(value: str) -> None:
    assert variable_accepts(VARIABLES, "custom_domain_name", value)


@pytest.mark.parametrize("value", CUSTOM_DOMAIN_NAMES_THAT_CANNOT_WORK)
def test_terraform_refuses_any_other_custom_domain_name(value: str) -> None:
    assert not variable_accepts(VARIABLES, "custom_domain_name", value)


def test_the_custom_domain_contract_is_documented() -> None:
    body = hcl_block(VARIABLES, 'variable "custom_domain_name"')
    description = (attribute(body, "description") or "").lower()
    messages = " ".join(
        (attribute(block, "error_message") or "").strip('"')
        for block in nested_blocks(body, "validation")
    ).lower()

    assert "docs.authifi.io" in description
    assert "docs.authifi.io" in messages
    assert "single" in description or "one site" in description or "fixed" in description


def test_the_public_base_url_contract_is_documented() -> None:
    body = hcl_block(VARIABLES, 'variable "public_base_url"')
    description = (attribute(body, "description") or "").lower()
    messages = " ".join(
        (attribute(block, "error_message") or "").strip('"')
        for block in nested_blocks(body, "validation")
    ).lower()

    assert "docs.authifi.io" in description
    assert "docs.authifi.io" in messages
    assert "mkdocs" in description or "static" in description or "authored" in description


@pytest.mark.parametrize("value", SITE_DIRS_THAT_WORK)
def test_terraform_accepts_the_release_layout_path(value: str) -> None:
    assert variable_accepts(VARIABLES, "site_dir", value)


@pytest.mark.parametrize("value", SITE_DIRS_THAT_CANNOT_WORK)
def test_terraform_refuses_any_other_site_dir(value: str) -> None:
    assert not variable_accepts(VARIABLES, "site_dir", value)


def test_the_site_dir_contract_is_documented() -> None:
    body = hcl_block(VARIABLES, 'variable "site_dir"')
    description = (attribute(body, "description") or "").lower()
    messages = " ".join(
        (attribute(block, "error_message") or "").strip('"')
        for block in nested_blocks(body, "validation")
    ).lower()

    assert "/opt/authifi-docs/current/site" in description
    assert "/opt/authifi-docs/current/site" in messages


@pytest.mark.parametrize("value", PUBLIC_BASE_URLS_THAT_CANNOT_WORK)
def test_terraform_refuses_a_public_base_url_the_host_could_not_honour(value: str) -> None:
    """Caught in a plan rather than after the instance is replaced.

    `public_base_url` reaches the host through user data, and user data is part
    of the instance's identity under `user_data_replace_on_change`. A value the
    server refuses at startup therefore costs a destroyed and rebuilt instance
    to correct, so the plan is where it has to fail.
    """
    assert not variable_accepts(VARIABLES, "public_base_url", value)


@pytest.mark.parametrize(
    "value", (*PUBLIC_BASE_URLS_THAT_WORK, *PUBLIC_BASE_URLS_THAT_CANNOT_WORK)
)
def test_terraform_never_accepts_a_public_base_url_the_server_would_refuse(
    value: str,
) -> None:
    """One rule, enforced twice, and the plan is the stricter of the two.

    `server/app.py` refuses a `PUBLIC_BASE_URL` carrying anything but the root
    path at startup. Terraform accepting one the server will reject is an
    instance that boots, replaces itself on the next apply, and never serves --
    so the interesting direction is this one: everything the plan allows has to
    be something the process will start on.
    """
    if variable_accepts(VARIABLES, "public_base_url", value):
        assert normalize_origin(value, allow_root_path=True) is not None


OIDC_ISSUERS_THAT_WORK = (
    "https://issuer.example.com",
    "https://issuer.authifi.io/tenants/authifi",
    "https://a.authifi.io/_api/auth/ls",
    "https://issuer.example.com/",
)

OIDC_ISSUERS_THAT_CANNOT_WORK = (
    "http://issuer.example.com",
    "https://",
    "issuer.example.com",
    "https://issuer.example.com?probe=1",
    "https://issuer.example.com/?probe=1",
    "https://issuer.example.com#fragment",
    "https://user:pass@issuer.example.com",
    "https://issuer.example.com//tenants/authifi",
)


@pytest.mark.parametrize("value", OIDC_ISSUERS_THAT_WORK)
def test_terraform_accepts_an_oidc_issuer_discovery_can_use(value: str) -> None:
    assert variable_accepts(VARIABLES, "oidc_issuer", value)


@pytest.mark.parametrize("value", OIDC_ISSUERS_THAT_CANNOT_WORK)
def test_terraform_refuses_an_oidc_issuer_discovery_could_not_use(value: str) -> None:
    assert not variable_accepts(VARIABLES, "oidc_issuer", value)


@pytest.mark.parametrize("value", (*OIDC_ISSUERS_THAT_WORK, *OIDC_ISSUERS_THAT_CANNOT_WORK))
def test_terraform_never_accepts_an_oidc_issuer_the_server_would_refuse(value: str) -> None:
    if variable_accepts(VARIABLES, "oidc_issuer", value):
        validate_oidc_issuer(value)


def test_the_chosen_instance_type_is_checked_against_aws() -> None:
    """Family-name regex misses oddball types and new families. The EC2 API's
    `supported_architectures` is the contract the AMI and wheelhouse depend on."""
    assert 'data "aws_ec2_instance_type" "selected"' in MAIN
    selected = hcl_block(MAIN, 'data "aws_ec2_instance_type" "selected"')
    assert attribute(selected, "instance_type") == "var.instance_type"


def test_the_instance_refuses_types_whose_architecture_is_not_x86_64() -> None:
    precondition = next(
        block
        for block in instance_preconditions()
        if "supported_architectures" in block
    )
    condition = attribute(precondition, "condition") or ""
    message = (attribute(precondition, "error_message") or "").lower()

    assert "data.aws_ec2_instance_type.selected.supported_architectures" in condition
    assert '"x86_64"' in condition
    assert "x86" in message or "amd64" in message


def test_the_instance_type_default_is_an_x86_family() -> None:
    body = hcl_block(VARIABLES, 'variable "instance_type"')
    assert attribute(body, "default") == '"t3.micro"'
    assert "x86_64" in (attribute(body, "description") or "").lower()


def test_the_certificate_is_not_blocked_on_validation_at_apply_time() -> None:
    """`aws_acm_certificate_validation` waits for records this root cannot
    create, so its presence would deadlock the very first apply."""
    assert 'resource "aws_acm_certificate_validation"' not in MAIN
    assert 'output "certificate_validation_records"' in OUTPUTS


# --- Deployment reaches the instance without SSH or a public address ---------


def test_ssm_download_urls_use_the_partition_dns_suffix() -> None:
    """Hard-coding `amazonaws.com` breaks GovCloud and other partitions."""
    document = hcl_block(MAIN, 'resource "aws_ssm_document" "deploy"')
    download_paths = re.findall(r'path\s*=\s*"([^"]+\.tar\.gz(?:\.sha256)?)"', document)

    assert len(download_paths) == 2
    for path in download_paths:
        assert "amazonaws.com" not in path
        assert "${data.aws_partition.current.dns_suffix}" in path


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
    assert actions(policy) == ["s3:GetObject", "ssm:GetParameter"]
    assert attribute(statement, "resources") == '["${aws_s3_bucket.releases.arn}/releases/*"]'


def test_oidc_secret_parameter_name_and_role_permissions_are_fixed() -> None:
    parameter_name = "/authifi-docs/oidc-client-secret"
    assert variable_accepts(
        VARIABLES, "oidc_client_secret_parameter_name", parameter_name
    )
    assert not variable_accepts(
        VARIABLES, "oidc_client_secret_parameter_name", f"{parameter_name}-other"
    )

    instance_policy = hcl_block(
        MAIN, 'data "aws_iam_policy_document" "instance_releases"'
    )
    instance_statement = statement_with_action(instance_policy, "ssm:GetParameter")
    deploy_policy = hcl_block(
        MAIN, 'data "aws_iam_policy_document" "github_deploy"'
    )
    deploy_statement = statement_with_action(deploy_policy, "ssm:PutParameter")

    expected_resource = "[local.oidc_client_secret_parameter_arn]"
    assert attribute(instance_statement, "resources") == expected_resource
    assert attribute(deploy_statement, "resources") == expected_resource
    assert "kms:" not in instance_policy
    assert "kms:" not in deploy_policy


def test_post_logout_path_defaults_to_the_logged_off_page() -> None:
    post_logout = hcl_block(VARIABLES, 'variable "post_logout_path"')

    assert attribute(post_logout, "default") == '"/logged-off"'
    assert variable_accepts(VARIABLES, "post_logout_path", "/logged-off")
    assert not variable_accepts(
        VARIABLES, "post_logout_path", "/guides/sso-integration-guide/"
    )


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
    assert 'resource "aws_s3_bucket_ownership_controls" "releases"' in MAIN
    assert 'resource "aws_s3_bucket_policy" "releases"' in MAIN


def test_the_release_bucket_disables_object_acls_entirely() -> None:
    """The public access block stops an ACL from making an object *public*. It
    says nothing about one granting a named account or an authenticated-users
    group, and an object ACL is an access decision made outside the two IAM
    policies this file enumerates. `BucketOwnerEnforced` removes the mechanism.
    """
    rule = hcl_block(
        hcl_block(MAIN, 'resource "aws_s3_bucket_ownership_controls" "releases"'), "rule"
    )

    assert attribute(rule, "object_ownership") == '"BucketOwnerEnforced"'


def test_the_release_bucket_refuses_requests_that_are_not_over_tls() -> None:
    """S3 answers plain HTTP, and nothing in an IAM policy or an endpoint URL
    prevents a client from using it. Releases are the code this host runs, so a
    request carrying one in the clear is both eavesdroppable and modifiable in
    flight; the bucket is the only place that can refuse it for every caller.

    The condition is what makes this safe to write as `Deny` on `s3:*` with a
    wildcard principal: without it, the same statement locks the account out of
    its own bucket.
    """
    policy = hcl_block(MAIN, 'data "aws_iam_policy_document" "releases_bucket"')
    blocks = statements(strip_comments(policy))

    # Deny-only, enumerated. An `Allow` in a resource policy is a grant that
    # none of the IAM assertions in this file would ever see.
    assert len(blocks) == 1
    assert [attribute(block, "effect") for block in blocks] == ['"Deny"']

    statement = blocks[0]
    condition = hcl_block(statement, "condition")

    assert attribute(condition, "test") == '"Bool"'
    assert attribute(condition, "variable") == '"aws:SecureTransport"'
    assert attribute(condition, "values") == '["false"]'

    # Both the bucket and its contents: a statement naming only one of the two
    # ARNs leaves the other reachable in the clear.
    assert attribute(statement, "resources") == (
        '[aws_s3_bucket.releases.arn, "${aws_s3_bucket.releases.arn}/*"]'
    )
    assert hcl_list(statement, "actions") == ["s3:*"]

    principals = hcl_block(statement, "principals")
    assert attribute(principals, "type") == '"*"'

    attachment = hcl_block(MAIN, 'resource "aws_s3_bucket_policy" "releases"')
    assert attribute(attachment, "policy") == "data.aws_iam_policy_document.releases_bucket.json"


def test_the_release_bucket_refuses_accidental_destruction_by_default() -> None:
    """The bucket is versioned and holds every release archive. Terraform's
    default is to refuse `destroy` while any object remains, which is the safe
    posture; emptying it has to be an explicit, separate step."""
    bucket = hcl_block(MAIN, 'resource "aws_s3_bucket" "releases"')

    assert attribute(bucket, "force_destroy") == "var.release_bucket_force_destroy"
    assert variable_default("release_bucket_force_destroy") == "false"
    assert "release_bucket_force_destroy" in TFVARS_EXAMPLE


def test_teardown_docs_apply_both_deletion_flags_before_destroy() -> None:
    """A versioned bucket and a deletion-protected load balancer each need one
    apply that opts in before `destroy` can succeed. The runbook names both."""
    section = INFRA_README.lower()
    assert "release_bucket_force_destroy" in section
    assert "enable_alb_deletion_protection" in section
    assert "destroy" in section

    apply_pos = section.index("release_bucket_force_destroy")
    destroy_pos = section.rindex("destroy")
    assert apply_pos < destroy_pos


def test_teardown_deletes_the_secret_from_the_terraform_region() -> None:
    region_capture = 'aws_region="$(terraform -chdir=infra output -raw aws_region)"'
    parameter_delete = "aws ssm delete-parameter"
    region_position = INFRA_README.index(region_capture)
    teardown_block = INFRA_README[
        INFRA_README.rindex("```bash", 0, region_position) :
        INFRA_README.index("```", INFRA_README.index(parameter_delete))
    ]

    assert region_capture in INFRA_README
    assert '--region "$aws_region"' in INFRA_README
    assert "--region us-east-1" not in INFRA_README
    assert teardown_block.splitlines()[1] == "set -euo pipefail"
    assert (
        INFRA_README.index(region_capture)
        < INFRA_README.index("terraform -chdir=infra destroy")
        < INFRA_README.index(parameter_delete)
    )


# --- The public edge, continued -----------------------------------------------


def test_the_load_balancer_cannot_be_deleted_by_accident() -> None:
    """The ALB owns the name users reach the docs through and the certificate
    that name is served under. Deleting it is a DNS-visible outage that a
    misdirected `terraform destroy` or a stray `-target` can cause in one
    command, and nothing about it is recoverable in place: the replacement has
    a different DNS name and needs the external record moved again.

    Teardown stays a normal Terraform operation, one apply longer.
    """
    alb = hcl_block(MAIN, 'resource "aws_lb" "docs"')

    assert attribute(alb, "enable_deletion_protection") == "var.enable_alb_deletion_protection"
    assert variable_default("enable_alb_deletion_protection") == "true"
    assert "enable_alb_deletion_protection" in TFVARS_EXAMPLE


def test_every_doc_that_mentions_the_target_wait_says_what_it_does_not_prove() -> None:
    """`aws elbv2 wait target-in-service` returns as soon as every target in
    the group reports `healthy`, and a target that was healthy before the swap
    can still be reporting healthy after it: the health check interval is
    longer than the swap takes. The wait therefore establishes that the load
    balancer can reach the instance and that `/health` answers through the
    target group. It does not establish which release answered.

    The route probes that follow do -- they fetch a public page and a protected
    page through the ALB itself. Every doc that mentions the wait has to say so,
    because the failure this invites is an operator reading a green wait as a
    successful deployment and stopping there.
    """
    for name, text in (
        ("README.md", README),
        ("infra/README.md", INFRA_README),
        ("operations/aws-oidc-hosting.md", OPERATIONS_DOC),
    ):
        lowered = text.lower()
        if "target health" not in lowered:
            continue

        assert "does not prove" in lowered, name
        assert "authoritative" in lowered, name


def test_the_absence_of_alb_access_logs_is_a_recorded_decision() -> None:
    """Not an oversight, and worth stating rather than leaving to be rediscovered.

    An access log of this load balancer is a per-request record of which
    protected documentation page each session read, which is a more sensitive
    artifact than the docs themselves and would live in a bucket with a
    different access model. The operational questions access logs usually
    answer -- is the target healthy, how many 5xx -- are already answered by
    target health and load balancer metrics, and the application's own log goes
    to the journal on the host.
    """
    alb = hcl_block(MAIN, 'resource "aws_lb" "docs"')

    assert not nested_blocks(strip_comments(alb), "access_logs")
    assert "access log" in operator_docs_text()


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
        "ssm:PutParameter",
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


def test_the_deploy_role_trusts_exactly_the_subject_the_workflow_presents() -> None:
    """The job declares `environment: production`, and that changes the token.

    GitHub's immutable subject names the owner and repository IDs plus the
    environment when a job references one, so the subject this workflow
    actually presents is
    `repo:<owner>@<owner-id>/<name>@<repo-id>:environment:<environment>`.
    A trust policy pinned to the ref form does not fail a plan, a validate, or
    any assertion about IAM shape: it fails the first real deployment with
    `Not authorized to perform sts:AssumeRoleWithWebIdentity`, after the
    release archive is already in S3.

    So the subject is derived here from the workflow's own `environment:` value
    rather than restated, and the two cannot drift apart silently.
    """
    environment = DEPLOY_WORKFLOW["jobs"]["deploy"]["environment"]
    conditions = github_trust_conditions()

    assert environment == variable_default("deploy_environment")

    subject = variable_default("github_repository_subject")
    assert conditions["sub"] == ("StringEquals", [subject])

    assert subject == "repo:Authifi@37509689/docs@993416679:environment:production"
    assert ":ref:" not in subject


def test_the_immutable_subject_must_match_its_companion_trust_inputs() -> None:
    role = hcl_block(MAIN, 'resource "aws_iam_role" "github_deploy"')
    lifecycle = hcl_block(role, "lifecycle")
    precondition = hcl_block(lifecycle, "precondition")
    condition = attribute(precondition, "condition")

    assert variable_default("github_repository_owner_id") == "37509689"
    assert condition == (
        'var.github_repository_subject == '
        '"repo:${split("/", var.github_repository)[0]}'
        '@${var.github_repository_owner_id}/'
        '${split("/", var.github_repository)[1]}'
        '@${var.github_repository_id}:environment:${var.deploy_environment}"'
    )


def test_immutable_subject_accepts_a_supported_environment_name_with_spaces() -> None:
    subject = "repo:Authifi@37509689/docs@993416679:environment:production west"

    assert variable_accepts(VARIABLES, "deploy_environment", "production west")
    assert variable_accepts(VARIABLES, "github_repository_subject", subject)


def test_the_deploy_role_binds_the_branch_and_the_repository_identity_too() -> None:
    """The subject alone is one string, and every part of it is a name someone
    can take. `ref` is what keeps a `production` deployment job on a branch
    other than the release branch out, and `repository_id` is what keeps a
    deleted-and-recreated `Authifi/docs` -- or a repository renamed into that
    path -- from inheriting the trust, since the numeric ID is not reusable.
    """
    triggers = deploy_workflow_triggers()
    branches = triggers["push"]["branches"]
    conditions = github_trust_conditions()

    assert branches == [variable_default("deploy_branch")]
    assert conditions["ref"] == ("StringEquals", [f"refs/heads/{branches[0]}"])
    assert conditions["repository_id"] == ("StringEquals", [variable_default("github_repository_id")])
    assert re.fullmatch(r"[0-9]+", variable_default("github_repository_id"))
    assert conditions["environment"] == (
        "StringEquals",
        [DEPLOY_WORKFLOW["jobs"]["deploy"]["environment"]],
    )


def test_every_trust_condition_is_an_exact_match_on_a_known_claim() -> None:
    """Enumerated, because the risk here is a condition nobody meant to add.

    `StringLike` on any of these turns an exact binding into a pattern, and a
    sixth condition on a claim not listed here is a claim this file has never
    reasoned about.
    """
    conditions = github_trust_conditions()

    assert set(conditions) == {"aud", "sub", "ref", "repository_id", "environment"}
    assert conditions["aud"] == ("StringEquals", ["sts.amazonaws.com"])

    for claim, (operator, values) in conditions.items():
        assert operator == "StringEquals", f"{claim} uses {operator}"
        assert len(values) == 1, f"{claim} accepts {values}"
        assert values[0].strip() == values[0] and values[0], claim
        assert "*" not in values[0] and "?" not in values[0], claim


# --- Host bootstrap -----------------------------------------------------------


def bootstrap_directories() -> dict[str, tuple[str, str, str]]:
    """Every directory the bootstrap installs, as path -> (mode, owner, group).

    Parsed from the `install -d` invocations rather than searched for, so a
    directory created without a declared mode or owner shows up as one with an
    empty field instead of quietly not being checked at all.
    """
    found: dict[str, tuple[str, str, str]] = {}

    for raw_line in user_data_statements().splitlines():
        line = raw_line.strip()
        if not line.startswith("install -d "):
            continue

        tokens = line.split()[2:]
        flags: dict[str, str] = {}
        paths: list[str] = []
        index = 0
        while index < len(tokens):
            if tokens[index] in ("-m", "-o", "-g") and index + 1 < len(tokens):
                flags[tokens[index]] = tokens[index + 1]
                index += 2
                continue
            paths.append(tokens[index])
            index += 1

        for path in paths:
            assert path not in found, f"{path} is installed twice"
            found[path] = (flags.get("-m", ""), flags.get("-o", ""), flags.get("-g", ""))

    return found


def test_the_release_tree_is_root_owned_and_unwritable_by_the_service_account() -> None:
    """The service account read the releases it runs, and owned them too.

    A service account that can write to the tree its own code is loaded from
    can replace that code and have systemd run it on the next restart, which
    turns any bug in the docs server -- a path traversal, a template injection,
    an unlucky dependency -- into persistence rather than a read. Nothing on
    this host needs that: releases are installed by root through Systems
    Manager, and the service only ever reads them.
    """
    directories = bootstrap_directories()
    release_tree = {
        path: attributes
        for path, attributes in directories.items()
        if path == "/opt/authifi-docs" or path.startswith("/opt/authifi-docs/")
    }

    assert set(release_tree) == {
        "/opt/authifi-docs",
        "/opt/authifi-docs/releases",
        "/opt/authifi-docs/incoming",
    }

    for path, (mode, owner, group) in release_tree.items():
        assert owner == "root", f"{path} is installed owned by {owner!r}"
        assert re.fullmatch(r"0[0-7]{3}", mode), f"{path} declares no explicit mode"
        # Read off the mode itself rather than compared to one string, so a
        # future 0751 or 0705 is judged on whether it grants write access.
        assert int(mode, 8) & 0o022 == 0, f"{path} is group- or other-writable at {mode}"

    # The service account traverses the top of the tree and reads releases, and
    # cannot see the staging directory at all: only root ever reads from there.
    assert release_tree["/opt/authifi-docs"] == ("0750", "root", "authifi-docs")
    assert release_tree["/opt/authifi-docs/incoming"] == ("0700", "root", "root")

    # And nothing later hands any of it back.
    bootstrap = user_data_statements()
    assert "-o authifi-docs" not in bootstrap
    assert not re.search(r"chown\s[^\n]*authifi-docs", bootstrap)


def test_the_service_unit_cannot_write_to_the_release_tree() -> None:
    """`ReadWritePaths=/opt/authifi-docs` punched a hole straight through
    `ProtectSystem=strict` for the whole release tree, which is the one
    directory the service must never be able to modify.

    Read from the unit with comments stripped, so the prose explaining why the
    setting is gone cannot satisfy the check.
    """
    bootstrap = user_data_statements()

    assert "ProtectSystem=strict" in bootstrap
    assert "ReadWritePaths" not in bootstrap
    assert "ReadOnlyPaths=/opt/authifi-docs" in bootstrap


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
        configuration = strip_comments(source)
        for permitted_name in (
            "OIDC_CLIENT_SECRET_PARAMETER_NAME",
            "oidc_client_secret_parameter_name",
            "oidc_client_secret_parameter_arn",
            "/authifi-docs/oidc-client-secret",
        ):
            configuration = configuration.replace(permitted_name, "")
        found = SECRET_MATERIAL.search(configuration)

        assert found is None, f"{filename} names secret material: {found and found[0]}"


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


# --- Terraform's values on their way onto the host ----------------------------
#
# These five reach the instance through user data and end up in the docs
# server's environment. They used to be interpolated into a file that
# `deploy-release.sh` loaded with `source`, which made every one of them root
# shell on the deployment path: an accepted `site_dir` carrying a space split
# into an assignment plus a command, and a command substitution or a semicolon
# in any value ran as root. Neither consumer evaluates anything now -- the
# installer parses JSON, and systemd's own `EnvironmentFile` is rendered from
# that JSON by one serializer on the host.

# Values a real deployment uses, and the base every hostile variant below is
# built from.
BOOTSTRAP_VALUES = {
    "aws_region": "us-east-1",
    "oidc_issuer": "https://issuer.authifi.io/tenants/authifi",
    "oidc_client_id": "authifi-docs",
    "oidc_client_secret_parameter_name": "/authifi-docs/oidc-client-secret",
    "public_base_url": "https://docs.authifi.io",
    "site_dir": "/opt/authifi-docs/current/site",
    "post_logout_path": "/logged-off",
    "app_port": "8080",
}

# Every way a value could try to become code in a shell that loaded it, plus
# the punctuation a legitimate path or URL is entitled to. Each one has to
# arrive in the service's environment byte for byte.
HOSTILE_VALUES = (
    "a value with spaces",
    "/opt/authifi docs/current/site",
    "$(touch CANARY)",
    "${CANARY}",
    "`touch CANARY`",
    "x; touch CANARY",
    "x && touch CANARY",
    "x | touch CANARY",
    "x > CANARY",
    'a "quoted" value',
    "a 'quoted' value",
    "a\\backslash",
    'trailing backslash and quote \\"',
    "a#hash",
    "a$dollar",
    "a=equals=sign",
    "  leading and trailing  ",
    "https://issuer.example/authorize?a=b&c=d#frag",
)


def host_config_mapping() -> dict[str, str]:
    """`local.host_config`, as environment variable name -> Terraform variable.

    Read from the HCL rather than restated, so a value added to the channel
    without a test is a value the renderer below does not know how to supply.
    """
    body = strip_comments(hcl_block(MAIN, "host_config ="))
    mapping = {
        match[1]: match[2]
        for match in re.finditer(
            r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*var\.([a-z_]+)\s*$", body, re.MULTILINE
        )
    }

    assert mapping, "local.host_config declares no NAME = var.name entries"
    return mapping


def test_the_bootstrap_template_uses_only_plain_variable_interpolations() -> None:
    """The tests below render this template in Python, and the emulation is
    only faithful if the template stays within what it emulates.

    `templatefile` also evaluates directives (`%{ if ... }`), function calls,
    and `$${` escapes, none of which a single substitution pass reproduces --
    so a template that grew one would be rendered wrongly by a test that then
    kept passing.
    """
    assert "%{" not in USER_DATA
    assert "$${" not in USER_DATA

    for interpolation in re.findall(r"\$\{([^}]*)\}", USER_DATA):
        assert re.fullmatch(r"[a-z_][a-z0-9_]*", interpolation), interpolation


def render_user_data(**overrides: str) -> str:
    """The bootstrap script `templatefile` would produce for these values."""
    values = {**BOOTSTRAP_VALUES, **overrides}
    inputs = {
        "config_json": json.dumps(
            {
                name: values[variable]
                for name, variable in host_config_mapping().items()
            }
        ),
        "app_port": values["app_port"],
    }
    declared = set(re.findall(r"\$\{([a-z_][a-z0-9_]*)\}", USER_DATA))

    assert declared == set(inputs), declared
    return re.sub(r"\$\{([a-z_][a-z0-9_]*)\}", lambda match: inputs[match[1]], USER_DATA)


def test_the_rendered_bootstrap_fits_in_the_space_ec2_gives_user_data() -> None:
    """16 KiB, and the instance simply fails to bootstrap past it."""
    assert len(render_user_data().encode("utf-8")) < 16384


CONFIG_SECTION_FIRST_LINE = "cat > /etc/authifi-docs/config.json"
CONFIG_SECTION_LAST_LINE = "chmod 0600 /etc/authifi-docs/environment"


@dataclass
class BootstrapHarness:
    """The part of the bootstrap that writes configuration, run for real.

    The rest of it installs packages, creates a system user, and enables a
    unit, so the section that turns Terraform's values into files is extracted
    and pointed at a temporary directory. The canary is how "no side effect"
    is asserted rather than assumed: a value carrying a command substitution
    names it, so anything that evaluated the value would create it.
    """

    tmp_path: Path
    etc: Path = field(init=False)
    canary: Path = field(init=False)
    result: subprocess.CompletedProcess[str] | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.etc = self.tmp_path / "etc" / "authifi-docs"
        self.etc.mkdir(parents=True)
        self.canary = self.tmp_path / "CANARY"

    def run(self, **overrides: str) -> subprocess.CompletedProcess[str]:
        rendered = render_user_data(
            **{
                name: value.replace("CANARY", str(self.canary))
                for name, value in overrides.items()
            }
        )
        lines = rendered.splitlines(keepends=True)
        start = next(
            index
            for index, line in enumerate(lines)
            if line.startswith(CONFIG_SECTION_FIRST_LINE)
        )
        end = next(
            index
            for index, line in enumerate(lines)
            if line.startswith(CONFIG_SECTION_LAST_LINE)
        )
        section = "".join(lines[start : end + 1])

        assert "config.json" in section and "environment" in section

        script = self.tmp_path / "write-config.sh"
        script.write_text(
            "set -euo pipefail\n"
            + section.replace("/etc/authifi-docs", str(self.etc)).replace(
                "python3 ", f"{sys.executable} "
            ),
            encoding="utf-8",
        )
        self.result = subprocess.run(
            ["bash", str(script)],
            cwd=self.tmp_path,
            capture_output=True,
            text=True,
        )
        return self.result

    @property
    def configuration(self) -> dict[str, object]:
        return json.loads((self.etc / "config.json").read_text(encoding="utf-8"))

    @property
    def environment_file(self) -> str:
        return (self.etc / "environment").read_text(encoding="utf-8")

    @property
    def systemd_environment(self) -> dict[str, str]:
        """The environment file decoded the way systemd documents it.

        Double-quoted, with backslash escapes, which is also POSIX shell's
        rule for the two characters that are escaped -- so `shlex` in POSIX
        mode is an independent decoder rather than a second copy of the
        renderer under test.
        """
        decoded: dict[str, str] = {}
        for line in self.environment_file.splitlines():
            name, separator, quoted = line.partition("=")

            assert separator, line
            assert quoted.startswith('"') and quoted.endswith('"'), line

            parts = shlex.split(quoted, posix=True)
            decoded[name] = parts[0] if parts else ""
        return decoded


@pytest.fixture
def bootstrap_harness(tmp_path: Path) -> BootstrapHarness:
    return BootstrapHarness(tmp_path)


def test_the_bootstrap_writes_the_configured_values_verbatim(
    bootstrap_harness: BootstrapHarness,
) -> None:
    result = bootstrap_harness.run()

    assert result.returncode == 0, result.stderr
    assert bootstrap_harness.configuration == {
        name: BOOTSTRAP_VALUES[variable]
        for name, variable in host_config_mapping().items()
    }
    assert bootstrap_harness.systemd_environment == bootstrap_harness.configuration


@pytest.mark.parametrize("value", HOSTILE_VALUES)
def test_a_shell_significant_value_survives_the_bootstrap_without_running(
    bootstrap_harness: BootstrapHarness, value: str
) -> None:
    """The reviewer's example is the space, and it is the mild one: an accepted
    absolute `site_dir` containing one used to split into an assignment plus a
    command. The command substitutions are the reason the fix is encoding
    rather than a metacharacter blacklist -- the list is never complete, and
    the legitimate values here are URLs and paths entitled to punctuation.
    """
    result = bootstrap_harness.run(site_dir=value, oidc_client_id=value)
    expected = value.replace("CANARY", str(bootstrap_harness.canary))

    assert result.returncode == 0, result.stderr
    assert bootstrap_harness.configuration["SITE_DIR"] == expected
    assert bootstrap_harness.configuration["OIDC_CLIENT_ID"] == expected
    assert bootstrap_harness.systemd_environment["SITE_DIR"] == expected
    assert bootstrap_harness.systemd_environment["OIDC_CLIENT_ID"] == expected
    assert not bootstrap_harness.canary.exists()


def test_the_environment_file_quotes_every_value_it_writes(
    bootstrap_harness: BootstrapHarness,
) -> None:
    """systemd performs no command substitution when it reads an
    `EnvironmentFile`, but an unquoted value carrying a space or a quote still
    parses as something other than what Terraform set."""
    bootstrap_harness.run(site_dir='/opt/a "b"\\c dir')

    lines = bootstrap_harness.environment_file.splitlines()

    assert lines, "the environment file is empty"
    for line in lines:
        assert re.fullmatch(r'[A-Z][A-Z0-9_]*="(?:[^"\\]|\\.)*"', line), line

    assert 'SITE_DIR="/opt/a \\"b\\"\\\\c dir"' in lines


@pytest.mark.parametrize("value", ["a\nb", "a\rb", "a\x00b", "a\x7fb", "a\tb"])
def test_a_control_character_fails_the_bootstrap_rather_than_truncating(
    bootstrap_harness: BootstrapHarness, value: str
) -> None:
    """An `EnvironmentFile` assignment cannot represent a newline at all, so a
    value carrying one would silently become a shorter value and, worse, a
    second assignment. Terraform refuses these at plan time; this is the check
    that does not depend on that one being complete.
    """
    result = bootstrap_harness.run(site_dir=value)

    assert result.returncode != 0
    assert "control character" in (result.stderr + result.stdout)
    assert not (bootstrap_harness.etc / "environment").exists()


def test_both_configuration_files_are_root_only(
    bootstrap_harness: BootstrapHarness,
) -> None:
    """systemd reads `EnvironmentFile` as root before dropping privileges, and
    the installer runs as root, so nothing here needs to be readable by the
    service account."""
    bootstrap_harness.run()

    for name in ("config.json", "environment"):
        mode = (bootstrap_harness.etc / name).stat().st_mode & 0o777

        assert mode == 0o600, f"{name} is {oct(mode)}"


def test_the_template_channel_is_one_json_document_and_the_port() -> None:
    """The template's inputs are enumerated rather than searched, because a
    second string input is a second thing interpolated into a file."""
    inputs = strip_comments(hcl_block(MAIN, TEMPLATE_INPUTS))

    assert set(re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", inputs, re.MULTILINE)) == {
        "config_json",
        "app_port",
    }
    assert attribute(inputs, "config_json") == "jsonencode(local.host_config)"
    assert set(host_config_mapping()) == {
        "AWS_REGION",
        "OIDC_ISSUER",
        "OIDC_CLIENT_ID",
        "OIDC_CLIENT_SECRET_PARAMETER_NAME",
        "PUBLIC_BASE_URL",
        "SITE_DIR",
        "POST_LOGOUT_PATH",
    }


def test_the_installer_never_evaluates_generated_configuration_as_shell() -> None:
    """`source` was the whole vulnerability. Nothing here loads a generated
    file as code any more, and the JSON one is what it reads instead."""
    installer = re.sub(
        r"(?m)^[ \t]*#.*$", "", (ROOT / "infra" / "scripts" / "deploy-release.sh").read_text(encoding="utf-8")
    )

    assert not re.search(r"(?m)^\s*source\s", installer)
    assert not re.search(r"(?m)^\s*\.\s+[\"'$/]", installer)
    assert "set -a" not in installer
    assert "config.json" in installer


@pytest.mark.parametrize("variable", sorted(set(host_config_mapping().values())))
def test_no_value_reaching_the_host_may_carry_a_control_character(variable: str) -> None:
    """Refused in the plan as well as on the host. The renderer fails the boot
    on one, and a value that fails the boot is one that costs a replaced
    instance to correct, because user data is part of the instance's identity.
    """
    accepted = BOOTSTRAP_VALUES[variable]

    assert variable_accepts(VARIABLES, variable, accepted), accepted
    for control in ("\n", "\r", "\x00", "\x1f", "\x7f"):
        assert not variable_accepts(VARIABLES, variable, accepted + control)
        assert not variable_accepts(VARIABLES, variable, control + accepted)


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


def test_docs_describe_confidential_pkce_registration_and_secret_source() -> None:
    text = OPERATIONS_DOC

    assert "confidential client" in text
    assert "PKCE S256" in text
    assert "client_secret_post" in text
    assert "https://docs.authifi.io/logged-off" in text
    assert "OIDC_CLIENT_SECRET" in INFRA_README
