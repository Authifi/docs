"""The smoke runner's URL overrides have to reach the stack they describe.

`--public-base-url` and `--mock-issuer` only ever configured the *client* half
of the smoke run. The Compose environment was built separately from `.env` and
the process environment, so `--public-base-url http://localhost:9001` left the
docs container published on 8000 and told it its own base URL was
`http://localhost:8000`, and `--mock-issuer http://elsewhere:9500` left the
provider listening on 9400 under its old alias. Every override therefore
produced a run that either could not connect at all or, worse, connected to a
stack configured for different URLs than the ones being asserted about.

That second failure mode got sharper once logout started checking `Origin`
against `PUBLIC_BASE_URL`: a client dialling one origin against a server told a
different one would have every sign-out refused, and the smoke would report a
CSRF regression that only existed in the harness.

So the overrides now build the Compose environment, and the settings are read
back out of that environment -- one direction, so the client cannot be told
something the stack was not. The URLs are validated as things this stack can
actually publish: cleartext HTTP, on loopback, on a port that can be bound
without privileges.
"""

from __future__ import annotations

import socket
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from server.local_smoke import (
    DEFAULT_MOCK_HOST,
    DEFAULT_MOCK_PORT,
    compose_env_for_args,
    host_is_alias_safe_name,
    host_is_loopback,
    parse_args,
    require_local_http_url,
    settings_for_args,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def args_for(argv: list[str], tmp_path: Path, environ: dict[str, str] | None = None):
    return parse_args(["--project-dir", str(tmp_path), *argv], environ=environ or {})


# RFC 6761 reserves `.localhost` for loopback, so these names need no lookup and
# no test here touches DNS. CI's real alias is `oidc-mock.local.test` from
# `/etc/hosts`; `test_a_mock_host_from_the_environment_survives_with_no_override`
# covers that shape with an injected resolver instead.
ALT_MOCK_HOST = "oidc-mock.alt.localhost"
ALT_MOCK_ISSUER = f"http://{ALT_MOCK_HOST}:9500"


# --- Validating a URL the local stack could actually serve --------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8000",
        "http://127.0.0.1:9001",
        "http://127.0.0.2:9001",
        "http://[::1]:9001",
        "http://localhost:65535",
        "http://localhost:1024",
    ],
)
def test_a_loopback_http_url_with_a_port_is_accepted(url: str) -> None:
    assert require_local_http_url(url, "--public-base-url").geturl() == url


REFUSED_URLS = {
    "https-has-no-terminator-here": "https://localhost:8000",
    "not-a-url": "localhost:8000",
    "no-scheme": "//localhost:8000",
    "no-port": "http://localhost",
    "privileged-port": "http://localhost:80",
    "port-zero": "http://localhost:0",
    "port-out-of-range": "http://localhost:70000",
    "unparseable-port": "http://localhost:notaport",
    "wildcard-address": "http://0.0.0.0:8000",
    "unspecified-v6": "http://[::]:8000",
    "lan-address": "http://192.168.1.10:8000",
    "public-address": "http://93.184.216.34:8000",
    "with-a-path": "http://localhost:8000/docs",
    "with-a-query": "http://localhost:8000?a=b",
    "with-a-fragment": "http://localhost:8000#top",
    "with-credentials": "http://user:pass@localhost:8000",
    "empty": "",
    "control-character": "http://localhost:8000\n",
}


@pytest.mark.parametrize("url", REFUSED_URLS.values(), ids=REFUSED_URLS)
def test_a_url_this_stack_could_not_serve_is_refused(url: str) -> None:
    """The message has to name the option, since the value came from the CLI."""
    with pytest.raises(ValueError, match="--public-base-url"):
        require_local_http_url(url, "--public-base-url")


def test_a_trailing_slash_is_accepted_as_the_empty_path_it_is() -> None:
    """`http://localhost:8000/` is the origin, not a sub-path deployment."""
    assert require_local_http_url("http://localhost:8000/", "--public-base-url").port == 8000


# --- Which hosts count as loopback --------------------------------------------


def resolver_for(addresses: dict[str, list[str]]):
    """A stand-in for `getaddrinfo`, so no test depends on DNS."""

    def resolve(host: str, port: int | None, *args, **kwargs):
        if host not in addresses:
            raise socket.gaierror(f"cannot resolve {host}")
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port or 0))
            for address in addresses[host]
        ]

    return resolve


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "127.0.0.53", "::1"])
def test_a_loopback_literal_or_localhost_needs_no_lookup(host: str) -> None:
    """Refusing to resolve at all proves these never reach the network."""

    def explode(*args, **kwargs):
        raise AssertionError("a loopback literal must not be resolved")

    assert host_is_loopback(host, resolve=explode)


def test_a_name_that_resolves_only_to_loopback_is_accepted() -> None:
    """How CI's `/etc/hosts` alias and the default `nip.io` name both qualify."""
    resolve = resolver_for({"oidc-mock.local.test": ["127.0.0.1"]})

    assert host_is_loopback("oidc-mock.local.test", resolve=resolve)


def test_a_name_that_resolves_anywhere_else_is_refused() -> None:
    """One routable address is enough: the published port is loopback-only, so
    a name pointing off-box describes a stack that cannot exist here."""
    resolve = resolver_for({"docs.example.com": ["127.0.0.1", "93.184.216.34"]})

    assert not host_is_loopback("docs.example.com", resolve=resolve)


def test_a_name_that_does_not_resolve_is_refused() -> None:
    assert not host_is_loopback("nowhere.invalid", resolve=resolver_for({}))


def test_a_name_that_resolves_to_nothing_at_all_is_refused() -> None:
    """An empty answer is not agreement."""
    assert not host_is_loopback("nowhere.test", resolve=lambda *a, **k: [])


def test_an_address_that_will_not_parse_is_refused() -> None:
    """A scoped IPv6 answer like `fe80::1%en0` fails closed rather than raising."""
    resolve = resolver_for({"scoped.test": ["fe80::1%en0"]})

    assert not host_is_loopback("scoped.test", resolve=resolve)


def test_the_host_is_matched_case_insensitively() -> None:
    """Hostnames are case-insensitive, and `urlsplit` already lowercases, so
    this only has to hold for a direct caller."""

    def explode(*args, **kwargs):
        raise AssertionError("a loopback literal must not be resolved")

    assert host_is_loopback("LOCALHOST", resolve=explode)


# --- The public base URL drives the port Compose publishes --------------------


def test_the_public_base_url_override_sets_both_the_url_and_the_port(tmp_path: Path) -> None:
    """The bug in one assertion: the port used to stay at its default."""
    args = args_for(["--public-base-url", "http://localhost:9001"], tmp_path)

    env = compose_env_for_args(args, environ={})

    assert env["PUBLIC_BASE_URL"] == "http://localhost:9001"
    assert env["DOCS_PORT"] == "9001"


def test_the_client_reads_its_base_url_back_out_of_the_compose_environment(
    tmp_path: Path,
) -> None:
    """The client cannot be pointed somewhere the stack was not configured for."""
    args = args_for(["--public-base-url", "http://127.0.0.1:9002"], tmp_path)

    env = compose_env_for_args(args, environ={})
    settings = settings_for_args(args, env)

    assert settings.public_base_url == env["PUBLIC_BASE_URL"]
    assert settings.origin == "http://127.0.0.1:9002"


def test_the_override_wins_over_a_dotenv_that_names_another_port(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("DOCS_PORT=8321\n", encoding="utf-8")
    args = args_for(["--public-base-url", "http://localhost:9003"], tmp_path)

    env = compose_env_for_args(args, environ={})

    assert env["DOCS_PORT"] == "9003"
    assert env["PUBLIC_BASE_URL"] == "http://localhost:9003"


def test_a_docs_port_from_the_environment_still_drives_the_default(tmp_path: Path) -> None:
    """No override given, so the documented environment route must still work."""
    args = args_for([], tmp_path, environ={"DOCS_PORT": "8456"})

    env = compose_env_for_args(args, environ={"DOCS_PORT": "8456"})

    assert env["DOCS_PORT"] == "8456"
    assert env["PUBLIC_BASE_URL"] == "http://localhost:8456"


def test_a_public_base_url_from_the_environment_also_fixes_the_port(tmp_path: Path) -> None:
    """The same inconsistency was reachable without the CLI at all: this env
    used to publish 8000 while telling the container it was on 9004."""
    environ = {"PUBLIC_BASE_URL": "http://localhost:9004"}
    args = args_for([], tmp_path, environ=environ)

    env = compose_env_for_args(args, environ=environ)

    assert env["DOCS_PORT"] == "9004"


def test_a_public_base_url_this_stack_cannot_serve_stops_the_run(tmp_path: Path) -> None:
    args = args_for(["--public-base-url", "https://docs.authifi.io"], tmp_path)

    with pytest.raises(ValueError, match="--public-base-url"):
        compose_env_for_args(args, environ={})


# --- The mock issuer drives the provider's alias and port ---------------------


def test_the_mock_issuer_override_sets_the_alias_and_the_port(tmp_path: Path) -> None:
    args = args_for(["--mock-issuer", ALT_MOCK_ISSUER], tmp_path)

    env = compose_env_for_args(args, environ={})

    assert env["MOCK_OIDC_HOST"] == ALT_MOCK_HOST
    assert env["MOCK_OIDC_PORT"] == "9500"


def test_the_client_and_the_stack_agree_on_the_issuer(tmp_path: Path) -> None:
    """OIDC requires one issuer URL for everybody. The docs container builds
    its own from these two variables, so they have to reconstruct the client's."""
    args = args_for(["--mock-issuer", ALT_MOCK_ISSUER], tmp_path)

    env = compose_env_for_args(args, environ={})
    settings = settings_for_args(args, env)

    assert settings.mock_issuer == f"http://{env['MOCK_OIDC_HOST']}:{env['MOCK_OIDC_PORT']}"
    assert settings.mock_issuer == ALT_MOCK_ISSUER
    assert settings.mock_host == ALT_MOCK_HOST
    assert settings.mock_port == "9500"


def test_a_mock_host_from_the_environment_survives_with_no_override(tmp_path: Path) -> None:
    """CI sets `MOCK_OIDC_HOST` and passes no flags at all, so the override has
    to be able to arrive as an argparse default and land back unchanged.

    The resolver stands in for the `/etc/hosts` line CI writes for this name.
    """
    environ = {"MOCK_OIDC_HOST": "oidc-mock.local.test"}
    args = args_for([], tmp_path, environ=environ)

    env = compose_env_for_args(
        args, environ=environ, resolve=resolver_for({"oidc-mock.local.test": ["127.0.0.1"]})
    )
    settings = settings_for_args(args, env)

    assert env["MOCK_OIDC_HOST"] == "oidc-mock.local.test"
    assert env["MOCK_OIDC_PORT"] == DEFAULT_MOCK_PORT
    assert settings.mock_issuer == f"http://oidc-mock.local.test:{DEFAULT_MOCK_PORT}"


def test_the_defaults_reconstruct_the_documented_local_stack(tmp_path: Path) -> None:
    """The resolver stands in for `nip.io`, which the default mock host uses to
    answer `127.0.0.1` and which is the one name here that needs real DNS."""
    args = args_for([], tmp_path)

    env = compose_env_for_args(
        args, environ={}, resolve=resolver_for({DEFAULT_MOCK_HOST: ["127.0.0.1"]})
    )
    settings = settings_for_args(args, env)

    assert env["MOCK_OIDC_HOST"] == DEFAULT_MOCK_HOST
    assert env["MOCK_OIDC_PORT"] == DEFAULT_MOCK_PORT
    assert env["DOCS_PORT"] == "8000"
    assert settings.public_base_url == "http://localhost:8000"
    assert settings.mock_issuer == f"http://{DEFAULT_MOCK_HOST}:{DEFAULT_MOCK_PORT}"


def test_a_mock_issuer_this_stack_cannot_serve_stops_the_run(tmp_path: Path) -> None:
    args = args_for(["--mock-issuer", "https://issuer.example.com"], tmp_path)

    with pytest.raises(ValueError, match="--mock-issuer"):
        compose_env_for_args(args, environ={})


# --- The mock issuer host has to be a name, not an address -------------------
#
# `MOCK_OIDC_HOST` is a Compose network alias as well as the host the smoke
# client dials, and OIDC requires both to agree on one issuer URL. An address
# cannot do that job. Inside the docs container `127.0.0.1` is the docs
# container, so discovery would dial the docs server -- or nothing -- instead of
# the provider, and the failure would look like a broken issuer rather than a
# bad argument. `::1` is worse: it is not a legal alias, and rebuilding
# `http://::1:9400` from a host and a port does not even produce a URL.
#
# `--public-base-url` is held to no such rule. Nothing resolves it inside a
# container; the host dials the published loopback port, so an address is the
# most natural thing to write there.

IP_LITERAL_ISSUERS = {
    "ipv4-loopback": "http://127.0.0.1:9400",
    "ipv4-other-loopback": "http://127.0.0.53:9400",
    "ipv4-lan": "http://192.168.1.10:9400",
    "ipv6-loopback": "http://[::1]:9400",
    "ipv6-loopback-long": "http://[0:0:0:0:0:0:0:1]:9400",
    "ipv6-unspecified": "http://[::]:9400",
}


@pytest.mark.parametrize("issuer", IP_LITERAL_ISSUERS.values(), ids=IP_LITERAL_ISSUERS)
def test_an_ip_literal_mock_issuer_stops_the_run(tmp_path: Path, issuer: str) -> None:
    args = args_for(["--mock-issuer", issuer], tmp_path)

    with pytest.raises(ValueError, match="--mock-issuer"):
        compose_env_for_args(args, environ={})


def test_the_refusal_of_an_address_explains_what_it_would_have_meant(
    tmp_path: Path,
) -> None:
    """The message has to be about the container, or the next person just picks
    a different address."""
    args = args_for(["--mock-issuer", "http://127.0.0.1:9400"], tmp_path)

    with pytest.raises(ValueError, match="hostname"):
        compose_env_for_args(args, environ={})


MALFORMED_ISSUER_HOSTS = {
    "scoped-ipv6": "http://[fe80::1%25en0]:9400",
    "trailing-dot": "http://oidc-mock.alt.localhost.:9400",
    "empty-label": "http://oidc..alt.localhost:9400",
    "leading-dot": "http://.alt.localhost:9400",
    "underscore": "http://oidc_mock.alt.localhost:9400",
    "leading-hyphen": "http://-oidc.alt.localhost:9400",
    "trailing-hyphen": "http://oidc-.alt.localhost:9400",
    "space": "http://oidc mock.alt.localhost:9400",
}


@pytest.mark.parametrize(
    "issuer", MALFORMED_ISSUER_HOSTS.values(), ids=MALFORMED_ISSUER_HOSTS
)
def test_a_mock_issuer_host_that_is_not_a_hostname_stops_the_run(
    tmp_path: Path, issuer: str
) -> None:
    args = args_for(["--mock-issuer", issuer], tmp_path)

    with pytest.raises(ValueError, match="--mock-issuer"):
        compose_env_for_args(args, environ={})


def test_a_label_longer_than_dns_allows_stops_the_run(tmp_path: Path) -> None:
    args = args_for(["--mock-issuer", f"http://{'a' * 64}.localhost:9400"], tmp_path)

    with pytest.raises(ValueError, match="--mock-issuer"):
        compose_env_for_args(args, environ={})


def test_a_dotted_localhost_name_is_accepted(tmp_path: Path) -> None:
    """The shape these tests use, and a legitimate one: RFC 6761 reserves it."""
    args = args_for(["--mock-issuer", ALT_MOCK_ISSUER], tmp_path)

    env = compose_env_for_args(args, environ={})

    assert env["MOCK_OIDC_HOST"] == ALT_MOCK_HOST


def test_a_custom_hostname_resolving_only_to_loopback_is_accepted(
    tmp_path: Path,
) -> None:
    """CI's `/etc/hosts` alias, which is a name and resolves to `127.0.0.1`."""
    args = args_for(["--mock-issuer", "http://oidc-mock.local.test:9400"], tmp_path)

    env = compose_env_for_args(
        args, environ={}, resolve=resolver_for({"oidc-mock.local.test": ["127.0.0.1"]})
    )

    assert env["MOCK_OIDC_HOST"] == "oidc-mock.local.test"


def test_a_hostname_that_resolves_off_loopback_still_stops_the_run(
    tmp_path: Path,
) -> None:
    """Being a name is necessary, not sufficient."""
    args = args_for(["--mock-issuer", "http://oidc.example.test:9400"], tmp_path)

    with pytest.raises(ValueError, match="loopback"):
        compose_env_for_args(
            args, environ={}, resolve=resolver_for({"oidc.example.test": ["93.184.216.34"]})
        )


def test_localhost_as_the_mock_issuer_stops_the_run(tmp_path: Path) -> None:
    """`localhost` resolves to loopback from the host, but inside the docs
    container it is the docs container itself, not the provider."""
    args = args_for(["--mock-issuer", "http://localhost:9400"], tmp_path)

    with pytest.raises(ValueError, match="--mock-issuer"):
        compose_env_for_args(args, environ={})


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("oidc-mock.alt.localhost", True),
        ("oidc-mock", True),
        ("OIDC-Mock.Alt.Localhost", True),
        ("a" * 63 + ".localhost", True),
        ("localhost", False),
        ("127.0.0.1", False),
        ("::1", False),
        ("fe80::1%en0", False),
        ("oidc-mock.alt.localhost.", False),
        ("oidc..alt", False),
        ("", False),
        ("_oidc", False),
        ("a" * 64, False),
        (("a" * 63 + ".") * 4 + "localhost", False),
    ],
)
def test_which_hosts_can_serve_as_a_compose_alias(host: str, expected: bool) -> None:
    assert host_is_alias_safe_name(host) is expected


# --- An address is still fine for the docs URL -------------------------------


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1:9001", "http://[::1]:9001", "http://localhost:9001"]
)
def test_the_public_base_url_still_accepts_an_address(tmp_path: Path, url: str) -> None:
    args = args_for(["--public-base-url", url], tmp_path)

    env = compose_env_for_args(args, environ={})

    assert env["PUBLIC_BASE_URL"] == url
    assert env["DOCS_PORT"] == "9001"


def test_a_bracketed_ipv6_docs_url_survives_being_taken_apart(tmp_path: Path) -> None:
    """The brackets are what make it a URL, so every value rebuilt from it has
    to keep them. `http://::1:9001` is not something a client can dial."""
    args = args_for(["--public-base-url", "http://[::1]:9001"], tmp_path)

    settings = settings_for_args(args, compose_env_for_args(args, environ={}))

    assert settings.origin == "http://[::1]:9001"
    assert settings.protected_url.startswith("http://[::1]:9001/")
    assert urlsplit(settings.origin).hostname == "::1"
    assert urlsplit(settings.origin).port == 9001


def test_a_scoped_ipv6_docs_url_is_refused(tmp_path: Path) -> None:
    """A zone index is meaningful only on the machine that wrote it, and is not
    something a published port mapping or a browser `Origin` can carry."""
    args = args_for(["--public-base-url", "http://[fe80::1%25en0]:9001"], tmp_path)

    with pytest.raises(ValueError, match="--public-base-url"):
        compose_env_for_args(args, environ={})


def test_a_mock_issuer_with_a_path_stops_the_run(tmp_path: Path) -> None:
    """The container builds `http://host:port` and appends its own paths, so a
    path here would silently be dropped from the stack's half of the issuer."""
    args = args_for(["--mock-issuer", f"{ALT_MOCK_ISSUER}/oidc"], tmp_path)

    with pytest.raises(ValueError, match="--mock-issuer"):
        compose_env_for_args(args, environ={})


# --- Nothing else about the mock run drifts ----------------------------------


def test_the_overrides_leave_the_pinned_mock_values_alone(tmp_path: Path) -> None:
    args = args_for(
        [
            "--public-base-url",
            "http://localhost:9005",
            "--mock-issuer",
            ALT_MOCK_ISSUER,
        ],
        tmp_path,
    )

    env = compose_env_for_args(args, environ={})

    assert env["POST_LOGOUT_PATH"] == "/privacy-policy/"
    for key in ("OIDC_ISSUER", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET", "SESSION_SECRET"):
        assert key not in env


def test_the_subject_override_still_reaches_the_client(tmp_path: Path) -> None:
    args = args_for(["--subject", "casey@example.com"], tmp_path)

    settings = settings_for_args(args, compose_env_for_args(args, environ={}))

    assert settings.subject == "casey@example.com"
    assert settings.user_url.endswith("/users/casey%40example.com")


def test_the_path_overrides_still_reach_the_client(tmp_path: Path) -> None:
    args = args_for(
        ["--public-path", "/privacy-policy/", "--protected-path", "/guides/"], tmp_path
    )

    settings = settings_for_args(args, compose_env_for_args(args, environ={}))

    assert settings.public_path == "/privacy-policy/"
    assert settings.protected_path == "/guides/"
