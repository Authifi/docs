from __future__ import annotations

import argparse
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import quote

import httpx


DEFAULT_PUBLIC_BASE_URL = "http://localhost:8000"
DEFAULT_PUBLIC_PATH = "/terms-of-service/"
DEFAULT_PROTECTED_PATH = "/"
DEFAULT_MOCK_ISSUER = "http://oidc-mock.127.0.0.1.nip.io:9400"
DEFAULT_SUBJECT = "alice@example.com"
DEFAULT_TIMEOUT_SECONDS = 60.0
POLL_INTERVAL_SECONDS = 0.5
COMPOSE_FILES = ("compose.yaml", "compose.mock.yaml")


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


def run_compose(project_dir: Path, *args: str) -> None:
    subprocess.run(
        build_compose_command(project_dir, COMPOSE_FILES, args),
        check=True,
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
    project_dir: Path,
    public_base_url: str,
    public_path: str,
    protected_path: str,
    mock_issuer: str,
    subject: str,
) -> None:
    public_url = f"{public_base_url.rstrip('/')}{public_path}"
    protected_url = f"{public_base_url.rstrip('/')}{protected_path}"
    login_url = f"{public_base_url.rstrip('/')}/_auth/login?next={quote(protected_path, safe='')}"
    logout_url = f"{public_base_url.rstrip('/')}/_auth/logout?next={quote(public_path, safe='')}"
    discovery_url = f"{mock_issuer.rstrip('/')}/.well-known/openid-configuration"
    user_url = f"{mock_issuer.rstrip('/')}/users/{quote(subject, safe='')}"

    with httpx.Client(timeout=10.0, trust_env=False) as client:
        wait_for_response(client, discovery_url, 200, "mock OIDC discovery")
        wait_for_response(client, public_url, 200, "public docs page")

        redirect_suffix = require_redirect(
            *extract_status_location(client.get(protected_url, follow_redirects=False)),
            expected_prefix="/_auth/login?next=",
        )
        if redirect_suffix != quote(protected_path, safe=""):
            raise AssertionError(
                f"expected protected redirect next to match {protected_path!r}, got {redirect_suffix!r}"
            )

        login_response = client.get(login_url, follow_redirects=False)
        authorize_url = require_redirect(
            login_response.status_code,
            login_response.headers.get("location"),
            f"{mock_issuer.rstrip('/')}/oauth2/authorize",
            expected_status=302,
        )

        client.put(
            user_url,
            json={"email": subject, "name": "Smoke Test User"},
        ).raise_for_status()

        authorize_response = client.post(
            f"{mock_issuer.rstrip('/')}/oauth2/authorize{authorize_url}",
            data={"sub": subject},
            follow_redirects=False,
        )
        callback_url = require_redirect(
            authorize_response.status_code,
            authorize_response.headers.get("location"),
            f"{public_base_url.rstrip('/')}/_auth/callback",
            expected_status=302,
        )

        callback_response = client.get(
            f"{public_base_url.rstrip('/')}/_auth/callback{callback_url}",
            follow_redirects=False,
        )
        require_redirect(
            callback_response.status_code,
            callback_response.headers.get("location"),
            protected_path,
        )

        protected_response = client.get(protected_url, follow_redirects=False)
        if protected_response.status_code != 200:
            raise AssertionError(
                f"expected authenticated protected page 200, got {protected_response.status_code}"
            )

        logout_response = client.get(logout_url, follow_redirects=False)
        require_redirect(
            logout_response.status_code,
            logout_response.headers.get("location"),
            public_path,
        )

        post_logout_redirect = require_redirect(
            *extract_status_location(client.get(protected_url, follow_redirects=False)),
            expected_prefix="/_auth/login?next=",
        )
        if post_logout_redirect != quote(protected_path, safe=""):
            raise AssertionError(
                f"expected logout to clear session for {protected_path!r}, got {post_logout_redirect!r}"
            )


def extract_status_location(response: httpx.Response) -> tuple[int, str | None]:
    return response.status_code, response.headers.get("location")


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Run local mock OIDC smoke test.")
    parser.add_argument("--project-dir", type=Path, default=repo_root)
    parser.add_argument("--public-base-url", default=DEFAULT_PUBLIC_BASE_URL)
    parser.add_argument("--public-path", default=DEFAULT_PUBLIC_PATH)
    parser.add_argument("--protected-path", default=DEFAULT_PROTECTED_PATH)
    parser.add_argument("--mock-issuer", default=DEFAULT_MOCK_ISSUER)
    parser.add_argument("--subject", default=DEFAULT_SUBJECT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        run_compose(args.project_dir, "up", "-d", "--build")
        run_smoke(
            project_dir=args.project_dir,
            public_base_url=args.public_base_url,
            public_path=args.public_path,
            protected_path=args.protected_path,
            mock_issuer=args.mock_issuer,
            subject=args.subject,
        )
    finally:
        run_compose(args.project_dir, "down", "--volumes", "--remove-orphans")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
