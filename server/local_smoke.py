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
DEFAULT_MOCK_CLIENT_ID = "local-docs-client"
DEFAULT_MOCK_CLIENT_SECRET = "local-docs-secret"
DEFAULT_SESSION_SECRET = "local-session-secret"
DEFAULT_TIMEOUT_SECONDS = 60.0
POLL_INTERVAL_SECONDS = 0.5
COMPOSE_FILES = ("compose.yaml", "compose.mock.yaml")


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

    mock_issuer = f"http://{merged_env['MOCK_OIDC_HOST']}:{merged_env['MOCK_OIDC_PORT']}"
    merged_env.setdefault("OIDC_ISSUER", mock_issuer)
    merged_env.setdefault("OIDC_CLIENT_ID", DEFAULT_MOCK_CLIENT_ID)
    merged_env.setdefault("OIDC_CLIENT_SECRET", DEFAULT_MOCK_CLIENT_SECRET)
    merged_env.setdefault("SESSION_SECRET", DEFAULT_SESSION_SECRET)
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
        wait_for_response(client, settings.public_url, 200, "public docs page")

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

        logout_response = client.get(settings.logout_url, follow_redirects=False)
        require_redirect(
            logout_response.status_code,
            logout_response.headers.get("location"),
            settings.public_path,
        )

        post_logout_redirect = require_redirect(
            *extract_status_location(client.get(settings.protected_url, follow_redirects=False)),
            expected_prefix="/_auth/login?next=",
        )
        if post_logout_redirect != quote(settings.protected_path, safe=""):
            raise AssertionError(
                f"expected logout to clear session for {settings.protected_path!r}, got {post_logout_redirect!r}"
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
