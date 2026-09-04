from __future__ import annotations

import argparse
import os
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx


DEFAULT_PUBLIC_PATH = "/terms-of-service/"
DEFAULT_PROTECTED_PATH = "/"
DEFAULT_DOCS_PORT = "8000"
DEFAULT_MOCK_HOST = "oidc-mock.127.0.0.1.nip.io"
DEFAULT_MOCK_PORT = "9400"
DEFAULT_SUBJECT = "alice@example.com"
DEFAULT_POST_LOGOUT_PATH = "/privacy-policy/"
DEFAULT_TIMEOUT_SECONDS = 60.0
POLL_INTERVAL_SECONDS = 0.5
COMPOSE_FILES = ("compose.yaml", "compose.mock.yaml")

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
        next_path = quote(self.public_path, safe="")
        return f"{self.public_base_url.rstrip('/')}/_auth/logout?next={next_path}"

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

        logout_mode = complete_logout(client, settings)
        print(f"logout completed via {logout_mode} flow")

        post_logout_redirect = require_redirect(
            *extract_status_location(client.get(settings.protected_url, follow_redirects=False)),
            expected_prefix="/_auth/login?next=",
        )
        if post_logout_redirect != quote(settings.protected_path, safe=""):
            raise AssertionError(
                f"expected logout to clear session for {settings.protected_path!r}, got {post_logout_redirect!r}"
            )


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
    if location.startswith(settings.public_path):
        return "local"
    if location.startswith(settings.mock_issuer.rstrip("/")):
        return "rp-initiated"
    raise AssertionError(f"unexpected logout redirect target {location!r}")


def complete_logout(client: httpx.Client, settings: SmokeSettings) -> str:
    """Run logout, tolerating both RP-initiated and local-fallback behaviour."""
    response = client.get(settings.logout_url, follow_redirects=False)
    if response.status_code != 307:
        raise AssertionError(f"expected logout status 307, got {response.status_code}")

    location = response.headers.get("location")
    mode = classify_logout_redirect(location, settings)
    if mode == "rp-initiated":
        issuer_response = client.get(str(location), follow_redirects=True)
        if issuer_response.status_code >= 400:
            raise AssertionError(
                f"RP-initiated logout endpoint returned {issuer_response.status_code}"
            )
    return mode


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
    compose_env = build_mock_compose_env(args.project_dir)
    settings = SmokeSettings(
        project_dir=args.project_dir,
        public_base_url=args.public_base_url,
        public_path=args.public_path,
        protected_path=args.protected_path,
        mock_host=compose_env["MOCK_OIDC_HOST"],
        mock_port=compose_env["MOCK_OIDC_PORT"],
        mock_issuer=args.mock_issuer,
        subject=args.subject,
    )
    try:
        run_compose(args.project_dir, compose_env, "up", "-d", "--build")
        run_smoke(settings)
    finally:
        run_compose(args.project_dir, compose_env, "down", "--volumes", "--remove-orphans")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
