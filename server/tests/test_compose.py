"""Networking guards for the local Compose stacks.

The mock issuer URL has to resolve for two different clients: the docs
container, which talks to the provider container, and the host, which runs the
smoke client against the published port. Getting that wrong fails only on Linux
-- Docker Desktop routes `host-gateway` to a loopback-bound published port, and
standard Linux engines do not -- so it is worth pinning here rather than
discovering it again in CI.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
BASE_COMPOSE = REPO_ROOT / "compose.yaml"
MOCK_COMPOSE = REPO_ROOT / "compose.mock.yaml"

DEFAULT_MOCK_HOST = "oidc-mock.127.0.0.1.nip.io"
CI_MOCK_HOST = "oidc-mock.local.test"

INTERPOLATION = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::-(?P<default>[^}]*))?\}")

docker_compose = pytest.mark.skipif(
    shutil.which("docker") is None, reason="docker CLI is not available"
)


def load_compose(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def interpolate(value: str, environ: dict[str, str]) -> str:
    """Resolve `${NAME}` and `${NAME:-default}` the way Compose does."""
    return INTERPOLATION.sub(
        lambda match: environ.get(match["name"]) or (match["default"] or ""), value
    )


def render_compose(environ: dict[str, str]) -> dict:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--project-directory",
            str(REPO_ROOT),
            "-f",
            str(BASE_COMPOSE),
            "-f",
            str(MOCK_COMPOSE),
            "config",
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(Path.home()), **environ},
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def service_aliases(service: dict) -> list[str]:
    networks = service.get("networks") or {}
    default = networks.get("default") or {}
    return list(default.get("aliases") or [])


# --- Static structure ---------------------------------------------------------


def test_no_service_routes_the_issuer_through_the_host_gateway() -> None:
    """`host-gateway` cannot reach a loopback-bound published port on Linux.

    Docker Desktop happens to make it work, which is exactly why this needs a
    test: the failure only appears on a standard Linux engine, as an auth 500.
    """
    for path in (BASE_COMPOSE, MOCK_COMPOSE):
        for name, service in load_compose(path)["services"].items():
            extra_hosts = service.get("extra_hosts") or []
            assert not any("host-gateway" in entry for entry in extra_hosts), (
                f"{path.name} service {name} still routes a host through host-gateway"
            )


def test_mock_provider_answers_to_the_issuer_hostname_on_the_compose_network() -> None:
    """The container path must not depend on the host at all."""
    services = load_compose(MOCK_COMPOSE)["services"]
    issuer = services["docs"]["environment"]["OIDC_ISSUER"]

    aliases = [interpolate(alias, {}) for alias in service_aliases(services["mock-oidc"])]

    assert urlsplit(interpolate(issuer, {})).hostname in aliases


@pytest.mark.parametrize(
    ("environ", "expected_host"),
    [({}, DEFAULT_MOCK_HOST), ({"MOCK_OIDC_HOST": CI_MOCK_HOST}, CI_MOCK_HOST)],
)
def test_alias_and_issuer_track_mock_oidc_host(
    environ: dict[str, str], expected_host: str
) -> None:
    services = load_compose(MOCK_COMPOSE)["services"]

    issuer = interpolate(services["docs"]["environment"]["OIDC_ISSUER"], environ)
    aliases = [interpolate(alias, environ) for alias in service_aliases(services["mock-oidc"])]

    assert urlsplit(issuer).hostname == expected_host
    assert expected_host in aliases


def test_mock_provider_publishes_only_on_loopback() -> None:
    """The host smoke client needs the port; the LAN does not."""
    ports = load_compose(MOCK_COMPOSE)["services"]["mock-oidc"]["ports"]

    assert all(port.startswith("127.0.0.1:") for port in ports)


# --- Rendered by Compose itself -----------------------------------------------


@docker_compose
def test_rendered_mock_stack_matches_the_ci_hostname() -> None:
    rendered = render_compose({"MOCK_OIDC_HOST": CI_MOCK_HOST})
    services = rendered["services"]

    assert CI_MOCK_HOST in service_aliases(services["mock-oidc"])
    assert services["docs"]["environment"]["OIDC_ISSUER"] == f"http://{CI_MOCK_HOST}:9400"


@docker_compose
def test_rendered_mock_stack_declares_no_extra_hosts() -> None:
    rendered = render_compose({"MOCK_OIDC_HOST": CI_MOCK_HOST})

    for name, service in rendered["services"].items():
        assert not service.get("extra_hosts"), f"service {name} rendered extra_hosts"


@docker_compose
def test_rendered_mock_stack_keeps_loopback_only_publication() -> None:
    rendered = render_compose({"MOCK_OIDC_HOST": CI_MOCK_HOST})

    for name, service in rendered["services"].items():
        for port in service.get("ports") or []:
            assert port.get("host_ip") == "127.0.0.1", f"service {name} publishes {port} beyond loopback"


@docker_compose
def test_rendered_default_mock_stack_uses_the_nip_io_hostname() -> None:
    rendered = render_compose({})

    assert DEFAULT_MOCK_HOST in service_aliases(rendered["services"]["mock-oidc"])
