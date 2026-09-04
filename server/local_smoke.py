from __future__ import annotations

import argparse
import ipaddress
import os
import re
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import SplitResult, parse_qs, quote, urlparse, urlsplit

import httpx


DEFAULT_PUBLIC_PATH = "/terms-of-service/"
DEFAULT_PROTECTED_PATH = "/"
DEFAULT_DOCS_PORT = "8000"
# The port each scheme implies, so an origin written without one still has a
# number to move away from in `origin_on_another_port`.
ORIGIN_DEFAULT_PORTS = {"http": 80, "https": 443}
DEFAULT_MOCK_HOST = "oidc-mock.127.0.0.1.nip.io"
DEFAULT_MOCK_PORT = "9400"
DEFAULT_SUBJECT = "alice@example.com"
DEFAULT_POST_LOGOUT_PATH = "/logged-off"
DEFAULT_TIMEOUT_SECONDS = 60.0
POLL_INTERVAL_SECONDS = 0.5
COMPOSE_FILES = ("compose.yaml", "compose.mock.yaml")
DIAGNOSTIC_SERVICES = ("docs", "mock-oidc")
DIAGNOSTIC_LOG_LINES = 200

# Both stacks publish to `127.0.0.1` only and serve cleartext, so a URL this
# runner is asked to use has to be one they could answer on. The port must be
# written out: it becomes a published mapping and the provider's own `--port`,
# and a scheme default of 80 would render a mapping that needs privileges to
# bind. 1024 is where binding stops needing them.
LOWEST_UNPRIVILEGED_PORT = 1024
HIGHEST_PORT = 65535
LOCAL_URL_SCHEME = "http"

# `303 See Other` is what the logout route answers, so that a browser which had
# just submitted the sign-out form arrives at the destination with a `GET`. Kept
# as a number here rather than imported: this module runs against a container
# and deliberately knows nothing about the application package. A test in
# `server/tests/test_local_smoke.py` holds the two equal.
EXPECTED_LOGOUT_STATUS = 303

# httpx normalises literal ".." segments away but leaves "%2e%2e" alone, so
# these probes reach the server exactly as written and prove that a public
# prefix cannot carry a request into protected content.
BYPASS_PROBE_PATHS = (
    "/assets/%2e%2e/index.html",
    "/assets/%2E%2E/index.html",
    "/assets/%2e%2e/search/search_index.json",
    "/javascripts/%2e%2e/index.html",
    "/stylesheets/%2e%2e/index.html",
    "/.well-known/%2e%2e/index.html",
    "/.well-known/%2e%2e/search/search_index.json",
)
PROTECTED_CONTENT_MARKERS = ("md-content__inner", '"docs":', "md-nav__link")
PUBLIC_MIME_PROBES = (
    ("/logged-off", "text/html; charset=utf-8"),
    ("/logged-off/", "text/html; charset=utf-8"),
    ("/privacy-policy/", "text/html; charset=utf-8"),
    ("/terms-of-service/", "text/html; charset=utf-8"),
    ("/sms-opt-in.html", "text/html; charset=utf-8"),
    ("/robots.txt", "text/plain; charset=utf-8"),
    ("/auth.md", "text/markdown; charset=utf-8"),
    ("/sitemap.xml", "application/xml"),
    ("/.well-known/agent-skills/index.json", "application/json"),
)
EXPECTED_PROTECTED_CONTENT_TYPE = "text/html; charset=utf-8"

# A protected directory route that exists, paired with one that does not. Both
# must look identical to an anonymous caller, otherwise a 308 to the
# trailing-slash form would confirm which pages the site holds.
EXISTENCE_PROBE_PATHS = (
    "/guides/sso-integration-guide",
    "/guides/definitely-not-a-real-guide",
)


@dataclass(frozen=True)
class SmokeSettings:
    project_dir: Path
    public_base_url: str
    public_path: str
    protected_path: str
    mock_host: str
    mock_port: str
    mock_issuer: str
    subject: str
    post_logout_path: str = DEFAULT_POST_LOGOUT_PATH

    @property
    def public_url(self) -> str:
        return f"{self.public_base_url.rstrip('/')}{self.public_path}"

    @property
    def protected_url(self) -> str:
        return f"{self.public_base_url.rstrip('/')}{self.protected_path}"

    @property
    def login_url(self) -> str:
        next_path = quote(self.protected_path, safe="")
        return f"{self.public_base_url.rstrip('/')}/_auth/login?next={next_path}"

    @property
    def logout_url(self) -> str:
        # `next` is passed deliberately. Logout must ignore it and use the
        # configured POST_LOGOUT_PATH, which is the URI registered with the
        # issuer, so a smoke run that sent no `next` would prove nothing.
        return f"{self.public_base_url.rstrip('/')}/_auth/logout?next={quote(self.public_path, safe='')}"

    @property
    def origin(self) -> str:
        """The `Origin` a browser on this site would send with the form POST."""
        parts = urlparse(self.public_base_url)
        return f"{parts.scheme}://{parts.netloc}"

    @property
    def post_logout_url(self) -> str:
        return f"{self.public_base_url.rstrip('/')}{self.post_logout_path}"

    @property
    def discovery_url(self) -> str:
        return f"{self.mock_issuer.rstrip('/')}/.well-known/openid-configuration"

    @property
    def user_url(self) -> str:
        return f"{self.mock_issuer.rstrip('/')}/users/{quote(self.subject, safe='')}"


def build_compose_command(
    project_dir: Path,
    compose_files: Sequence[str],
    args: Sequence[str],
) -> list[str]:
    command = ["docker", "compose", "--project-directory", str(project_dir)]
    for compose_file in compose_files:
        command.extend(["-f", str(project_dir / compose_file)])
    command.extend(args)
    return command


def read_env_file(env_path: Path) -> dict[str, str]:
    if not env_path.is_file():
        return {}

    env: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, _, value = stripped.partition("=")
        if not key or not _:
            continue
        env[key] = value
    return env


def build_mock_compose_env(project_dir: Path, environ: dict[str, str] | None = None) -> dict[str, str]:
    merged_env = read_env_file(project_dir / ".env")
    merged_env.update(os.environ if environ is None else environ)

    merged_env.setdefault("DOCS_PORT", DEFAULT_DOCS_PORT)
    merged_env.setdefault("MOCK_OIDC_HOST", DEFAULT_MOCK_HOST)
    merged_env.setdefault("MOCK_OIDC_PORT", DEFAULT_MOCK_PORT)
    merged_env.setdefault("MOCK_OIDC_SUBJECT", DEFAULT_SUBJECT)
    merged_env.setdefault("PUBLIC_BASE_URL", f"http://localhost:{merged_env['DOCS_PORT']}")
    # compose.yaml applies the same default, but pinning it here keeps the mock
    # run deterministic even when a developer's .env points the real stack
    # somewhere else.
    merged_env.setdefault("POST_LOGOUT_PATH", DEFAULT_POST_LOGOUT_PATH)
    return merged_env


Resolver = Callable[..., list]

# One DNS label: letters, digits and hyphens, not starting or ending with one.
# Underscores are excluded deliberately -- they appear in service records, not
# in host names, and Compose aliases are host names.
DNS_LABEL = re.compile(r"(?!-)[a-z0-9-]{1,63}(?<!-)\Z", re.IGNORECASE)
MAX_HOSTNAME_LENGTH = 253


def host_is_alias_safe_name(host: str) -> bool:
    """Whether `host` is a name that can also serve as a Compose alias.

    The mock issuer's host has two jobs at once: the smoke client dials it from
    this machine, and `compose.mock.yaml` hangs it on the provider as a network
    alias so the docs container can reach the provider under the same name. OIDC
    requires one issuer URL for everybody, and only a name can be both.

    An address cannot. Inside the docs container `127.0.0.1` is the docs
    container, so discovery would dial the docs server instead of the provider,
    and `::1` is not a legal alias at all -- nor does rebuilding
    `http://::1:9400` from a host and a port produce a URL. The bare name
    `localhost` is refused for the same reason: inside the docs container it
    names the docs container, not the provider. Names ending in `.localhost`
    are still accepted.

    A trailing dot is refused too. It is a legitimate way to write an absolute
    name, but it is not the same string as the name without it, and this value
    is compared as a string in a Compose alias and an issuer URL.
    """
    if not host or len(host) > MAX_HOSTNAME_LENGTH:
        return False
    if host.lower() == "localhost":
        return False
    if host.endswith("."):
        return False
    # An address is not a name, in either family. A scoped or otherwise
    # unparseable v6 form is caught by the label rules below.
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    return all(DNS_LABEL.match(label) for label in host.split("."))


def host_is_loopback(host: str, resolve: Resolver = socket.getaddrinfo) -> bool:
    """Whether `host` names this machine and nothing else.

    Both Compose stacks publish on `127.0.0.1` only, so a host that reaches
    anywhere else describes a stack that cannot exist here. A literal address is
    judged as itself, and `localhost` is taken as given -- neither touches the
    network, which is also what keeps the tests off DNS.

    A name is looked up, because the two loopback names this project actually
    uses cannot be recognised any other way: `oidc-mock.127.0.0.1.nip.io` by
    default and `oidc-mock.local.test` from CI's `/etc/hosts`. Every address it
    resolves to must be loopback -- one routable answer is enough to refuse,
    since that is the one a client might pick -- and a name that does not
    resolve is refused rather than assumed.
    """
    host = host.lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True

    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        pass

    try:
        addresses = resolve(host, None, 0, socket.SOCK_STREAM)
    except OSError:
        return False
    return bool(addresses) and all(
        address_is_loopback(entry[4][0]) for entry in addresses
    )


def address_is_loopback(address: str) -> bool:
    """One resolved address, failing closed on anything unreadable.

    A scoped IPv6 answer such as `fe80::1%en0` will not parse, and a link-local
    address is not loopback anyway, so there is nothing to salvage.
    """
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def require_local_http_url(
    value: str,
    option: str,
    resolve: Resolver = socket.getaddrinfo,
    host_must_be_a_name: bool = False,
) -> SplitResult:
    """`value` parsed, or a `ValueError` naming the option it came from.

    Refuses anything the local stacks could not serve, rather than letting the
    run fail later as a connection error or -- worse -- as a stack quietly
    configured for URLs other than the ones being asserted about. The loopback
    rule is also what keeps this runner pointed at a throwaway stack: it tears
    the stack down with `--volumes` when it finishes and it writes a user into
    the issuer it is given, neither of which belongs anywhere but here.

    `host_must_be_a_name` is for the mock issuer, whose host doubles as a
    Compose network alias; see `host_is_alias_safe_name`. The docs URL is not
    held to it, because nothing resolves that name inside a container -- the
    host dials a published loopback port, so an address is the natural value.
    """

    def refuse(reason: str) -> ValueError:
        return ValueError(f"{option}: {reason}, got {value!r}")

    if not value or any(character in value for character in "\r\n\t"):
        raise refuse("must be an absolute URL")

    parts = urlsplit(value)
    if parts.scheme != LOCAL_URL_SCHEME:
        # No TLS terminator exists in either stack, so `https` would be a URL
        # nothing here can answer.
        raise refuse(f"must be a {LOCAL_URL_SCHEME}:// URL")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise refuse("must be an origin with no path, query, or fragment")
    if parts.username or parts.password:
        raise refuse("must not carry credentials")

    try:
        host, port = parts.hostname, parts.port
    except ValueError:
        raise refuse("has a port that is not a number") from None
    if not host:
        raise refuse("must name a host")
    if port is None:
        raise refuse("must name a port explicitly")
    if not LOWEST_UNPRIVILEGED_PORT <= port <= HIGHEST_PORT:
        raise refuse(
            f"must name a port between {LOWEST_UNPRIVILEGED_PORT} and {HIGHEST_PORT}"
        )
    if host_must_be_a_name and not host_is_alias_safe_name(host):
        raise refuse(
            "must name a DNS hostname, not an address: it is also the provider's "
            "Compose network alias, and inside the docs container an address "
            "points at the docs container"
        )
    if not host_is_loopback(host, resolve=resolve):
        raise refuse("must name a host that resolves only to loopback")
    return parts


def compose_env_for_args(
    args: argparse.Namespace,
    environ: dict[str, str] | None = None,
    resolve: Resolver = socket.getaddrinfo,
) -> dict[str, str]:
    """The Compose environment that serves the URLs this run will dial.

    The overrides used to configure only the smoke client: the Compose
    environment was built separately, so `--public-base-url http://localhost:9001`
    published 8000 and told the container it lived there. Deriving the
    environment from the arguments is what makes the stack and the client the
    same decision, and it fixes the same inconsistency reached through the
    environment alone -- `PUBLIC_BASE_URL` without a matching `DOCS_PORT`.

    Both directions still work because the arguments default to what `.env` and
    the process environment say, so CI setting `MOCK_OIDC_HOST` and passing no
    flags arrives here as an argument and lands back in the environment
    unchanged.
    """
    env = build_mock_compose_env(args.project_dir, environ)

    docs_url = require_local_http_url(args.public_base_url, "--public-base-url", resolve)
    issuer_url = require_local_http_url(
        args.mock_issuer, "--mock-issuer", resolve, host_must_be_a_name=True
    )

    env["PUBLIC_BASE_URL"] = args.public_base_url
    env["DOCS_PORT"] = str(docs_url.port)
    # `compose.mock.yaml` builds `OIDC_ISSUER`, the network alias, the published
    # mapping, and the provider's `--port` out of these two, so setting them is
    # what makes the container's issuer and the client's the same URL.
    env["MOCK_OIDC_HOST"] = issuer_url.hostname
    env["MOCK_OIDC_PORT"] = str(issuer_url.port)
    return env


def settings_for_args(args: argparse.Namespace, compose_env: dict[str, str]) -> SmokeSettings:
    """The client settings, read back out of the environment the stack got.

    One direction, deliberately: anything the client dials is something the
    stack was configured for. It matters more since logout began checking
    `Origin` against `PUBLIC_BASE_URL` -- a client on one origin against a
    server told another would see every sign-out refused and report a CSRF
    regression that existed only in the harness.
    """
    return SmokeSettings(
        project_dir=args.project_dir,
        public_base_url=compose_env["PUBLIC_BASE_URL"],
        public_path=args.public_path,
        protected_path=args.protected_path,
        mock_host=compose_env["MOCK_OIDC_HOST"],
        mock_port=compose_env["MOCK_OIDC_PORT"],
        mock_issuer=f"http://{compose_env['MOCK_OIDC_HOST']}:{compose_env['MOCK_OIDC_PORT']}",
        subject=args.subject,
        post_logout_path=compose_env["POST_LOGOUT_PATH"],
    )


def resolve_settings(
    project_dir: Path,
    environ: dict[str, str] | None = None,
    public_path: str = DEFAULT_PUBLIC_PATH,
    protected_path: str = DEFAULT_PROTECTED_PATH,
) -> SmokeSettings:
    env = build_mock_compose_env(project_dir, environ)
    return SmokeSettings(
        project_dir=project_dir,
        public_base_url=env["PUBLIC_BASE_URL"],
        public_path=public_path,
        protected_path=protected_path,
        mock_host=env["MOCK_OIDC_HOST"],
        mock_port=env["MOCK_OIDC_PORT"],
        mock_issuer=f"http://{env['MOCK_OIDC_HOST']}:{env['MOCK_OIDC_PORT']}",
        subject=env["MOCK_OIDC_SUBJECT"],
        post_logout_path=env["POST_LOGOUT_PATH"],
    )


def require_redirect(
    status_code: int,
    location: str | None,
    expected_prefix: str,
    expected_status: int = 307,
) -> str:
    if status_code != expected_status:
        raise AssertionError(f"expected status {expected_status}, got {status_code}")
    if not location or not location.startswith(expected_prefix):
        raise AssertionError(
            f"expected location starting with {expected_prefix!r}, got {location!r}"
        )
    return location.removeprefix(expected_prefix)


def assert_no_protected_content(path: str, status_code: int, body: str) -> None:
    if status_code != 404:
        raise AssertionError(f"expected 404 for bypass probe {path!r}, got {status_code}")
    for marker in PROTECTED_CONTENT_MARKERS:
        if marker in body:
            raise AssertionError(f"bypass probe {path!r} leaked protected content marker {marker!r}")


def assert_content_type(path: str, actual: str | None, expected: str) -> None:
    if actual != expected:
        raise AssertionError(f"expected {path!r} to be served as {expected!r}, got {actual!r}")


def run_compose(project_dir: Path, env: dict[str, str], *args: str) -> None:
    subprocess.run(
        build_compose_command(project_dir, COMPOSE_FILES, args),
        check=True,
        env=env,
    )


def compose_diagnostics(project_dir: Path) -> list[list[str]]:
    """Commands whose output explains why a smoke run failed."""
    commands = [build_compose_command(project_dir, COMPOSE_FILES, ("ps",))]
    for service in DIAGNOSTIC_SERVICES:
        commands.append(
            build_compose_command(
                project_dir,
                COMPOSE_FILES,
                ("logs", "--no-color", f"--tail={DIAGNOSTIC_LOG_LINES}", service),
            )
        )
    return commands


def dump_diagnostics(project_dir: Path, env: dict[str, str], runner=subprocess.run) -> None:
    """Print container state and logs to stderr, before the stack is removed.

    Best effort by design: this runs while an assertion is already propagating,
    so a failure to collect evidence must never replace the failure that
    triggered it.
    """
    for command in compose_diagnostics(project_dir):
        print(f"\n$ {' '.join(command)}", file=sys.stderr, flush=True)
        try:
            result = runner(command, env=env, check=False, capture_output=True, text=True)
        except Exception as error:  # noqa: BLE001 - diagnostics must not mask the real error
            print(f"(could not collect diagnostics: {error})", file=sys.stderr, flush=True)
            continue
        output = f"{result.stdout or ''}{result.stderr or ''}".rstrip()
        print(output or "(no output)", file=sys.stderr, flush=True)


def wait_for_response(
    client: httpx.Client,
    url: str,
    expected_status: int,
    description: str,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> httpx.Response:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = client.get(url, follow_redirects=False)
        except httpx.HTTPError as error:
            last_error = error
        else:
            if response.status_code == expected_status:
                return response
            last_error = AssertionError(
                f"{description} returned {response.status_code}, expected {expected_status}"
            )
        time.sleep(POLL_INTERVAL_SECONDS)

    raise TimeoutError(f"timed out waiting for {description}: {last_error}")


def run_smoke(
    settings: SmokeSettings,
) -> None:
    with httpx.Client(timeout=10.0, trust_env=False) as client:
        wait_for_response(client, settings.discovery_url, 200, "mock OIDC discovery")
        public_response = wait_for_response(client, settings.public_url, 200, "public docs page")
        assert_content_type(
            settings.public_path,
            public_response.headers.get("content-type"),
            EXPECTED_PROTECTED_CONTENT_TYPE,
        )

        for path, expected_content_type in PUBLIC_MIME_PROBES:
            probe = client.get(f"{settings.public_base_url.rstrip('/')}{path}", follow_redirects=False)
            if probe.status_code != 200:
                raise AssertionError(f"expected public path {path!r} to return 200, got {probe.status_code}")
            assert_content_type(path, probe.headers.get("content-type"), expected_content_type)

        for path in BYPASS_PROBE_PATHS:
            probe = client.get(f"{settings.public_base_url.rstrip('/')}{path}", follow_redirects=False)
            assert_no_protected_content(path, probe.status_code, probe.text)

        assert_no_existence_disclosure(
            {
                path: client.get(
                    f"{settings.public_base_url.rstrip('/')}{path}", follow_redirects=False
                )
                for path in EXISTENCE_PROBE_PATHS
            }
        )

        redirect_suffix = require_redirect(
            *extract_status_location(client.get(settings.protected_url, follow_redirects=False)),
            expected_prefix="/_auth/login?next=",
        )
        if redirect_suffix != quote(settings.protected_path, safe=""):
            raise AssertionError(
                f"expected protected redirect next to match {settings.protected_path!r}, got {redirect_suffix!r}"
            )

        sign_in(client, settings)

        protected_response = client.get(settings.protected_url, follow_redirects=False)
        if protected_response.status_code != 200:
            raise AssertionError(
                f"expected authenticated protected page 200, got {protected_response.status_code}"
            )
        assert_content_type(
            settings.protected_path,
            protected_response.headers.get("content-type"),
            EXPECTED_PROTECTED_CONTENT_TYPE,
        )

        for path in BYPASS_PROBE_PATHS:
            probe = client.get(f"{settings.public_base_url.rstrip('/')}{path}", follow_redirects=False)
            assert_no_protected_content(path, probe.status_code, probe.text)

        assert_only_a_post_can_sign_out(client, settings)
        assert_a_foreign_form_cannot_sign_out(client, settings)

        logout_mode = complete_logout(client, settings)
        print(f"logout completed via {logout_mode} flow")

        assert_anonymous_logout_stays_local(client, settings)
        assert_an_anonymous_browser_also_lands_on_the_landing_page(client, settings)

        post_logout_redirect = require_redirect(
            *extract_status_location(client.get(settings.protected_url, follow_redirects=False)),
            expected_prefix="/_auth/login?next=",
        )
        if post_logout_redirect != quote(settings.protected_path, safe=""):
            raise AssertionError(
                f"expected logout to clear session for {settings.protected_path!r}, got {post_logout_redirect!r}"
            )

        # A second sign-in, because the checks above spent the first one. This
        # is the only way to watch a browser follow the *authenticated* logout,
        # which is the flow that goes through the issuer.
        sign_in(client, settings)
        assert_a_browser_completes_the_rp_sign_out(client, settings)


def assert_no_existence_disclosure(probes: dict[str, httpx.Response]) -> None:
    """Anonymous replies for existing and missing protected routes must match."""
    for path, response in probes.items():
        location = response.headers.get("location", "")
        if response.status_code != 307 or not location.startswith("/_auth/login?next="):
            raise AssertionError(
                f"expected {path!r} to answer 307 to the login redirect, "
                f"got {response.status_code} -> {location!r}"
            )
        if location != f"/_auth/login?next={quote(path, safe='')}":
            raise AssertionError(f"login redirect for {path!r} did not echo the request: {location!r}")

    shapes = {(response.status_code, response.text) for response in probes.values()}
    if len(shapes) > 1:
        raise AssertionError("anonymous replies differ between existing and missing protected routes")


def classify_logout_redirect(location: str | None, settings: SmokeSettings) -> str:
    """Return "local" or "rp-initiated" for a logout redirect target."""
    if not location:
        raise AssertionError("logout response did not include a Location header")
    if location == settings.post_logout_path:
        return "local"
    if location.startswith(settings.mock_issuer.rstrip("/")):
        return "rp-initiated"
    raise AssertionError(f"unexpected logout redirect target {location!r}")


def assert_registered_post_logout_uri(location: str, settings: SmokeSettings) -> None:
    """The issuer must be handed the exact registered URI, never a caller's `next`."""
    params = parse_qs(urlparse(location).query)
    redirect_uris = params.get("post_logout_redirect_uri", [])
    if redirect_uris != [settings.post_logout_url]:
        raise AssertionError(
            f"expected post_logout_redirect_uri {settings.post_logout_url!r}, got {redirect_uris!r}"
        )
    if not params.get("client_id"):
        raise AssertionError(f"RP-initiated logout is missing client_id: {location!r}")


def sign_in(client: httpx.Client, settings: SmokeSettings) -> None:
    """Drive one complete authorization-code sign-in against the mock issuer.

    A function rather than a stretch of `run_smoke` because the run signs in
    more than once: ending a session is what the logout assertions do, so
    anything that has to be checked with a live session needs a fresh one.
    """
    login_response = client.get(settings.login_url, follow_redirects=False)
    authorize_url = require_redirect(
        login_response.status_code,
        login_response.headers.get("location"),
        f"{settings.mock_issuer.rstrip('/')}/oauth2/authorize",
        expected_status=302,
    )

    client.put(
        settings.user_url,
        json={"email": settings.subject, "name": "Smoke Test User"},
    ).raise_for_status()

    authorize_response = client.post(
        f"{settings.mock_issuer.rstrip('/')}/oauth2/authorize{authorize_url}",
        data={"sub": settings.subject},
        follow_redirects=False,
    )
    callback_url = require_redirect(
        authorize_response.status_code,
        authorize_response.headers.get("location"),
        f"{settings.public_base_url.rstrip('/')}/_auth/callback",
        expected_status=302,
    )

    callback_response = client.get(
        f"{settings.public_base_url.rstrip('/')}/_auth/callback{callback_url}",
        follow_redirects=False,
    )
    require_redirect(
        callback_response.status_code,
        callback_response.headers.get("location"),
        settings.protected_path,
    )


def origin_on_another_port(origin: str) -> str:
    """The same scheme and host as `origin`, on a neighbouring port.

    For the probe that checks a same-site, different-port submission is refused
    -- the case `SameSite=Lax` treats as its own site and the `Origin` check has
    to catch. It has to be a *valid* origin: appending a digit, which is what
    this replaced, turned `http://localhost:8000` into `http://localhost:80009`,
    and the server then refused it for being unparseable rather than for being
    the wrong port. The probe passed while testing nothing.

    A host is re-bracketed if it needs it, since `http://::1:8001` is not a URL.
    The neighbour is one above, or one below at the top of the range, so the
    result is always a port that could exist -- `urlsplit` will not hand back
    anything outside 0 to 65535, so there is no floor to guard.
    """
    parts = urlsplit(origin)
    host = parts.hostname
    if host is None:
        raise ValueError(f"cannot build a port probe from {origin!r}: it names no host")

    try:
        port = parts.port
    except ValueError:
        raise ValueError(f"cannot build a port probe from {origin!r}: its port is not a number") from None
    if port is None:
        port = ORIGIN_DEFAULT_PORTS.get(parts.scheme, LOWEST_UNPRIVILEGED_PORT)

    other = port - 1 if port >= HIGHEST_PORT else port + 1
    if ":" in host:
        host = f"[{host}]"
    return f"{parts.scheme}://{host}:{other}"


def post_logout(client: httpx.Client, settings: SmokeSettings, origin: str | None):
    """Submit the sign-out form, optionally from somewhere else."""
    headers = {} if origin is None else {"Origin": origin}
    return client.post(settings.logout_url, headers=headers, follow_redirects=False)


def assert_only_a_post_can_sign_out(client: httpx.Client, settings: SmokeSettings) -> None:
    """A `GET` must not end the session, whoever or whatever issued it.

    Run while signed in, so "the session survived" is something the following
    request can actually establish.
    """
    response = client.get(settings.logout_url, follow_redirects=False)
    if response.status_code != 405:
        raise AssertionError(f"expected GET logout to answer 405, got {response.status_code}")
    if "POST" not in response.headers.get("allow", ""):
        raise AssertionError(f"405 did not name POST in Allow: {response.headers.get('allow')!r}")
    assert_still_signed_in(client, settings, "a GET to the logout route")


def assert_a_foreign_form_cannot_sign_out(client: httpx.Client, settings: SmokeSettings) -> None:
    """The CSRF check, against the origins a forged submission would carry."""
    for description, origin in (
        ("another site", "http://attacker.invalid"),
        ("no origin at all", None),
        ("our host on the wrong port", origin_on_another_port(settings.origin)),
        ("an origin that is not a URL", "definitely not an origin"),
    ):
        response = post_logout(client, settings, origin)
        if response.status_code != 403:
            raise AssertionError(
                f"expected a logout POST from {description} to answer 403, "
                f"got {response.status_code}"
            )
        assert_still_signed_in(client, settings, f"a logout POST from {description}")


def assert_still_signed_in(client: httpx.Client, settings: SmokeSettings, after: str) -> None:
    response = client.get(settings.protected_url, follow_redirects=False)
    if response.status_code != 200:
        raise AssertionError(
            f"{after} ended the session: protected page answered {response.status_code}"
        )


def complete_logout(client: httpx.Client, settings: SmokeSettings) -> str:
    """Run the documented production logout and assert it lands where it should."""
    response = post_logout(client, settings, settings.origin)
    if response.status_code != EXPECTED_LOGOUT_STATUS:
        raise AssertionError(
            f"expected logout status {EXPECTED_LOGOUT_STATUS}, got {response.status_code}"
        )

    location = response.headers.get("location")
    mode = classify_logout_redirect(location, settings)
    if mode == "rp-initiated":
        assert_registered_post_logout_uri(str(location), settings)
        issuer_response = client.get(str(location), follow_redirects=True)
        if issuer_response.status_code >= 400:
            raise AssertionError(
                f"RP-initiated logout endpoint returned {issuer_response.status_code}"
            )
    return mode


def assert_a_browser_completes_the_rp_sign_out(
    client: httpx.Client, settings: SmokeSettings
) -> None:
    """Sign out the way a browser does, and follow it the way a browser would.

    The status is the point. A `307` preserves the method, so the browser would
    have re-sent the POST to whatever came next -- the landing page, whose route
    serves `GET` and answers `405`, or the issuer's end-session endpoint, where
    accepting a POST is the tenant's decision. This walks the real chain and
    fails if any hop after the submission is not a `GET`.

    Where the chain ends is the issuer's business, not ours: an end-session
    endpoint may return its own page rather than bouncing on to
    `post_logout_redirect_uri`, and the mock does exactly that. So this asserts
    the method of every hop, a final `200`, and a session that is gone --
    `assert_an_anonymous_browser_also_lands_on_the_landing_page` is what pins
    the local flow to our own landing page.

    Needs a live session and spends it, so it runs last.
    """
    response = client.post(
        settings.logout_url,
        headers={"Origin": settings.origin},
        follow_redirects=True,
    )

    hops = [(hop.request.method, str(hop.request.url)) for hop in response.history]
    hops.append((response.request.method, str(response.request.url)))
    if response.status_code != 200:
        raise AssertionError(
            f"expected a followed sign-out to land on 200, got {response.status_code}: {hops}"
        )
    if hops[0][0] != "POST":
        raise AssertionError(f"the sign-out itself was not a POST: {hops}")
    if len(hops) < 2:
        raise AssertionError(f"the sign-out did not redirect anywhere: {hops}")
    for method, url in hops[1:]:
        if method != "GET":
            raise AssertionError(f"a browser would have re-sent {method} to {url}: {hops}")

    probe = client.get(settings.protected_url, follow_redirects=False)
    if probe.status_code != 307:
        raise AssertionError(
            f"the session survived a followed sign-out: protected page answered "
            f"{probe.status_code}"
        )
    print(f"a browser's sign-out followed {len(hops) - 1} GET hop(s) to a 200 and cleared the session")


def assert_anonymous_logout_stays_local(client: httpx.Client, settings: SmokeSettings) -> None:
    """With no session there is nothing to end, so no outbound issuer hop."""
    response = post_logout(client, settings, settings.origin)
    location = response.headers.get("location")
    if response.status_code != EXPECTED_LOGOUT_STATUS or location != settings.post_logout_path:
        raise AssertionError(
            f"expected anonymous logout to redirect to {settings.post_logout_path!r}, "
            f"got {response.status_code} -> {location!r}"
        )


def assert_an_anonymous_browser_also_lands_on_the_landing_page(
    client: httpx.Client, settings: SmokeSettings
) -> None:
    """The local fallback, followed. No session, so no issuer hop is involved,
    which makes this the branch that would have 405'd most often."""
    response = client.post(
        settings.logout_url,
        headers={"Origin": settings.origin},
        follow_redirects=True,
    )
    if response.status_code != 200 or response.request.method != "GET":
        raise AssertionError(
            f"expected an anonymous followed sign-out to land on 200 via GET, got "
            f"{response.status_code} via {response.request.method} at {response.request.url}"
        )


def extract_status_location(response: httpx.Response) -> tuple[int, str | None]:
    return response.status_code, response.headers.get("location")


def parse_args(
    argv: Sequence[str] | None = None,
    environ: dict[str, str] | None = None,
) -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parent.parent
    bootstrap_parser = argparse.ArgumentParser(add_help=False)
    bootstrap_parser.add_argument("--project-dir", type=Path, default=repo_root)
    bootstrap_args, _ = bootstrap_parser.parse_known_args(argv)
    defaults = resolve_settings(bootstrap_args.project_dir, environ)

    parser = argparse.ArgumentParser(description="Run local mock OIDC smoke test.")
    parser.add_argument("--project-dir", type=Path, default=bootstrap_args.project_dir)
    parser.add_argument("--public-base-url", default=defaults.public_base_url)
    parser.add_argument("--public-path", default=DEFAULT_PUBLIC_PATH)
    parser.add_argument("--protected-path", default=DEFAULT_PROTECTED_PATH)
    parser.add_argument("--mock-issuer", default=defaults.mock_issuer)
    parser.add_argument("--subject", default=defaults.subject)
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    compose_env = compose_env_for_args(args)
    settings = settings_for_args(args, compose_env)
    try:
        run_compose(args.project_dir, compose_env, "up", "-d", "--build")
        run_smoke(settings)
    except BaseException:
        # Before `down` removes the containers, so a CI failure carries the
        # evidence for it instead of just an assertion message.
        dump_diagnostics(args.project_dir, compose_env)
        raise
    finally:
        run_compose(args.project_dir, compose_env, "down", "--volumes", "--remove-orphans")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
