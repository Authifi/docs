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

from server import local_smoke

REPO_ROOT = Path(__file__).resolve().parents[2]
BASE_COMPOSE = REPO_ROOT / "compose.yaml"
MOCK_COMPOSE = REPO_ROOT / "compose.mock.yaml"

DEFAULT_MOCK_HOST = "oidc-mock.127.0.0.1.nip.io"
CI_MOCK_HOST = "oidc-mock.local.test"
DEFAULT_MOCK_PORT = "9400"
CUSTOM_MOCK_PORT = "9500"

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


@docker_compose
@pytest.mark.parametrize("port", [DEFAULT_MOCK_PORT, CUSTOM_MOCK_PORT])
def test_mock_oidc_port_reaches_every_place_the_port_appears(port: str) -> None:
    """`MOCK_OIDC_PORT` has to move the provider, not just the host mapping.

    The issuer URL carries this port, and since the docs container now dials the
    provider directly on the Compose network, the provider has to be listening
    on it. Publishing `9500:9400` would satisfy the host and strand the
    container.
    """
    services = render_compose({"MOCK_OIDC_HOST": CI_MOCK_HOST, "MOCK_OIDC_PORT": port})["services"]
    mock = services["mock-oidc"]

    assert services["docs"]["environment"]["OIDC_ISSUER"] == f"http://{CI_MOCK_HOST}:{port}"

    command = mock["command"]
    assert port in command, f"provider is not told to listen on {port}: {command}"
    assert command[command.index(port) - 1] in ("--port", "-p")

    published = mock["ports"]
    assert [(p["host_ip"], p["published"], str(p["target"])) for p in published] == [
        ("127.0.0.1", port, port)
    ]

    healthcheck = " ".join(mock["healthcheck"]["test"])
    assert f"127.0.0.1:{port}/" in healthcheck, healthcheck


# --- Rendered from what the smoke CLI would actually pass ---------------------
#
# The tests above render Compose from hand-written environments. These render it
# from the environment `server.local_smoke` builds out of its own arguments,
# which is the thing that was broken: the overrides configured the smoke client
# and never reached the stack, so `--public-base-url http://localhost:9001`
# published 8000 and told the container it lived there.



# RFC 6761 reserves `.localhost` for loopback, so the smoke runner accepts these
# without a lookup and these tests need no DNS. The default `nip.io` host does
# need one, so the no-override case stands in for it.
SMOKE_MOCK_HOST = "oidc-mock.alt.localhost"
LOOPBACK_ADDRESSES = [(2, 1, 6, "", ("127.0.0.1", 0))]


def smoke_args(argv: list[str]):
    return local_smoke.parse_args(["--project-dir", str(REPO_ROOT), *argv], environ={})


def smoke_compose_env(argv: list[str]) -> dict[str, str]:
    return local_smoke.compose_env_for_args(
        smoke_args(argv), environ={}, resolve=lambda *args, **kwargs: LOOPBACK_ADDRESSES
    )


@docker_compose
def test_a_custom_public_base_url_moves_the_published_docs_port() -> None:
    services = render_compose(smoke_compose_env(["--public-base-url", "http://localhost:9001"]))[
        "services"
    ]

    published = [(p["host_ip"], p["published"], str(p["target"])) for p in services["docs"]["ports"]]
    assert published == [("127.0.0.1", "9001", "8080")]


@docker_compose
def test_a_custom_public_base_url_is_what_the_container_is_told_it_is() -> None:
    """`PUBLIC_BASE_URL` is what logout checks `Origin` against, so a stack
    told the default while the client dials 9001 refuses every sign-out."""
    env = smoke_compose_env(["--public-base-url", "http://localhost:9001"])
    services = render_compose(env)["services"]

    assert services["docs"]["environment"]["PUBLIC_BASE_URL"] == "http://localhost:9001"


@docker_compose
def test_a_custom_public_base_url_leaves_the_issuer_alone() -> None:
    services = render_compose(smoke_compose_env(["--public-base-url", "http://localhost:9001"]))[
        "services"
    ]

    assert services["docs"]["environment"]["OIDC_ISSUER"] == (
        f"http://{DEFAULT_MOCK_HOST}:{DEFAULT_MOCK_PORT}"
    )


@docker_compose
def test_a_custom_mock_issuer_moves_the_alias_the_port_and_the_issuer() -> None:
    """Every place the issuer appears has to name the same host and port: the
    container dials it on the Compose network, the host dials the published
    port, and the provider itself has to be listening on it."""
    issuer = f"http://{SMOKE_MOCK_HOST}:{CUSTOM_MOCK_PORT}"
    services = render_compose(smoke_compose_env(["--mock-issuer", issuer]))["services"]
    mock = services["mock-oidc"]

    assert services["docs"]["environment"]["OIDC_ISSUER"] == issuer
    assert SMOKE_MOCK_HOST in service_aliases(mock)
    assert CUSTOM_MOCK_PORT in mock["command"]
    assert [(p["host_ip"], p["published"], str(p["target"])) for p in mock["ports"]] == [
        ("127.0.0.1", CUSTOM_MOCK_PORT, CUSTOM_MOCK_PORT)
    ]
    assert f"127.0.0.1:{CUSTOM_MOCK_PORT}/" in " ".join(mock["healthcheck"]["test"])


@docker_compose
def test_both_overrides_at_once_render_one_coherent_stack() -> None:
    issuer = f"http://{SMOKE_MOCK_HOST}:{CUSTOM_MOCK_PORT}"
    argv = ["--public-base-url", "http://127.0.0.1:9002", "--mock-issuer", issuer]
    env = smoke_compose_env(argv)
    settings = local_smoke.settings_for_args(smoke_args(argv), env)
    services = render_compose(env)["services"]

    # What the smoke client will dial, against what the stack will answer on.
    assert services["docs"]["environment"]["PUBLIC_BASE_URL"] == settings.public_base_url
    assert services["docs"]["environment"]["OIDC_ISSUER"] == settings.mock_issuer
    assert services["docs"]["ports"][0]["published"] == str(
        urlsplit(settings.public_base_url).port
    )
    assert SMOKE_MOCK_HOST in service_aliases(services["mock-oidc"])


@docker_compose
def test_the_rendered_default_stack_still_matches_the_documented_one() -> None:
    """No overrides: the CLI-built environment must not change today's stack."""
    services = render_compose(smoke_compose_env([]))["services"]

    assert services["docs"]["environment"]["PUBLIC_BASE_URL"] == "http://localhost:8000"
    assert [(p["host_ip"], p["published"]) for p in services["docs"]["ports"]] == [
        ("127.0.0.1", "8000")
    ]
    assert DEFAULT_MOCK_HOST in service_aliases(services["mock-oidc"])
    assert services["docs"]["environment"]["OIDC_ISSUER"] == (
        f"http://{DEFAULT_MOCK_HOST}:{DEFAULT_MOCK_PORT}"
    )
