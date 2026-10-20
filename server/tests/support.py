"""Shared fixtures and helpers for the docs server tests."""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any, Mapping
from unittest.mock import patch
from urllib.parse import urlsplit

from itsdangerous import TimestampSigner
from starlette.responses import RedirectResponse
from starlette.testclient import TestClient

from server.app import (
    SESSION_AUTHENTICATED_AT_KEY,
    SESSION_USER_KEY,
    AppConfig,
    create_app,
)

SESSION_SECRET = "session-secret"
SESSION_COOKIE_NAME = "authifi-session"
DEFAULT_PUBLIC_BASE_URL = "https://docs.example.com"

# Distinguishes "use the real clock" from an explicit `None`, which is one of
# the malformed timestamps the server has to refuse.
_CURRENT = object()

HEADERS_FILE = (
    "/\n"
    '  Link: </.well-known/api-catalog>; rel="api-catalog"\n'
    '  Link: </guides/nhe-delegated-tokens/>; rel="service-doc"\n'
    '  Link: </guides/sso-integration-guide/>; rel="service-doc"\n'
    '  Link: </security/recommended-secure-configuration/>; rel="service-doc"\n'
)

SITE_FILES: Mapping[str, str] = {
    "index.html": "<h1>Private home</h1>",
    "logged-off/index.html": (
        "<h1>You’ve been logged off</h1>"
        '<a href="/_auth/login">Sign in to Authifi docs</a>'
    ),
    "privacy-policy/index.html": "<h1>Privacy</h1>",
    "terms-of-service/index.html": "<h1>Terms</h1>",
    "sms-opt-in.html": "<h1>SMS</h1>",
    "sitemap.xml": "<urlset></urlset>",
    "robots.txt": "User-agent: *\nAllow: /\n",
    "auth.md": "# Auth\n",
    ".well-known/api-catalog": '{"links":[]}',
    ".well-known/agent-skills/index.json": '{"skills":[]}',
    "assets/app.css": "body{}",
    "assets/app.js": "console.log('assets');",
    "assets/logo.png": "not-really-a-png",
    "javascripts/app.js": "console.log('ok');",
    "stylesheets/app.css": "body{}",
    "search/search_index.json": '{"docs":["protected-search-entry"]}',
    "guides/sso-integration-guide/index.html": "<h1>SSO integration guide</h1>",
    "security/index.html": "<h1>Security overview</h1>",
    "_headers": HEADERS_FILE,
}

PROTECTED_MARKERS = (
    "Private home",
    "protected-search-entry",
    "SSO integration guide",
    "Security overview",
)


def write_site(root: Path) -> Path:
    """Materialise the canonical fake built site used across server tests."""
    for relative_path, content in SITE_FILES.items():
        target = root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return root


class DummyAuthClient:
    """Minimal stand-in for the Authlib OIDC client."""

    def __init__(
        self,
        token: dict | None = None,
        metadata: dict[str, Any] | None = None,
        metadata_error: Exception | None = None,
    ) -> None:
        self.token = token or {
            "userinfo": {
                "sub": "user-123",
                "email": "user@example.com",
                "name": "Example User",
                "role": "admin",
            },
            "id_token": "opaque-id-token",
            "access_token": "opaque-access-token",
        }
        self.metadata = metadata if metadata is not None else {}
        self.metadata_error = metadata_error
        self.redirect_calls: list[dict] = []
        self.token_requests = 0
        self.metadata_requests = 0

    async def authorize_redirect(self, request, redirect_uri, **kwargs):
        self.redirect_calls.append({"redirect_uri": redirect_uri, **kwargs})
        return RedirectResponse("https://issuer.example.com/authorize")

    async def authorize_access_token(self, request):
        self.token_requests += 1
        return self.token

    async def load_server_metadata(self) -> dict[str, Any]:
        self.metadata_requests += 1
        if self.metadata_error is not None:
            raise self.metadata_error
        return self.metadata


class NoDiscoveryAuthClient(DummyAuthClient):
    """Auth client without metadata discovery support at all."""

    load_server_metadata = None  # type: ignore[assignment]


def build_config(
    site_dir: Path,
    public_base_url: str = DEFAULT_PUBLIC_BASE_URL,
    **overrides: Any,
) -> AppConfig:
    return AppConfig(
        oidc_issuer="https://issuer.example.com",
        oidc_client_id="client-id",
        oidc_client_secret="client-secret",
        session_secret=SESSION_SECRET,
        public_base_url=public_base_url,
        site_dir=site_dir,
        **overrides,
    )


def build_app(
    site_dir: Path,
    public_base_url: str = DEFAULT_PUBLIC_BASE_URL,
    auth_client: DummyAuthClient | None = None,
    **overrides: Any,
):
    config = build_config(site_dir, public_base_url=public_base_url, **overrides)
    return create_app(config=config, auth_client=auth_client or DummyAuthClient())


def build_client(
    site_dir: Path,
    public_base_url: str = DEFAULT_PUBLIC_BASE_URL,
    auth_client: DummyAuthClient | None = None,
    raise_server_exceptions: bool = True,
    **overrides: Any,
) -> TestClient:
    return TestClient(
        build_app(site_dir, public_base_url, auth_client, **overrides),
        raise_server_exceptions=raise_server_exceptions,
    )


def signed_in_session(
    user: dict | None = None,
    authenticated_at: Any = _CURRENT,
    **extra: Any,
) -> dict:
    """A session the server will accept as signed in.

    The authentication time is part of what makes a session valid, so building
    one without it is building an expired session. Pass ``authenticated_at``
    explicitly to build a stale, forged, or pre-existing one on purpose.
    """
    session: dict[str, Any] = {
        SESSION_USER_KEY: user if user is not None else {"sub": "user-123"},
        SESSION_AUTHENTICATED_AT_KEY: (
            int(time.time()) if authenticated_at is _CURRENT else authenticated_at
        ),
    }
    session.update(extra)
    return session


def authenticated_client(
    site_dir: Path,
    public_base_url: str = DEFAULT_PUBLIC_BASE_URL,
    auth_client: DummyAuthClient | None = None,
    session: dict | None = None,
    **overrides: Any,
) -> TestClient:
    client = build_client(site_dir, public_base_url=public_base_url, auth_client=auth_client, **overrides)
    client.cookies.set(SESSION_COOKIE_NAME, encode_session_cookie(with_authentication_time(session)))
    return client


def with_authentication_time(session: dict | None) -> dict:
    """Stamp a hand-built session that carries a user but not a time.

    Callers that name a session are usually saying something about one key --
    a pending login, a stale user -- and should not have to restate what makes
    a session valid. One that sets the time itself is left alone.
    """
    if session is None:
        return signed_in_session()
    if SESSION_USER_KEY not in session or SESSION_AUTHENTICATED_AT_KEY in session:
        return session
    return {**session, SESSION_AUTHENTICATED_AT_KEY: int(time.time())}


def sign_out(
    client: TestClient,
    public_base_url: str = DEFAULT_PUBLIC_BASE_URL,
    params: dict | None = None,
):
    """Sign out the way a browser does: a form POST from the site's own origin.

    Going through one helper means every test exercises the same request the
    rendered form sends, and that only one place needs changing if it moves.
    """
    return client.post(
        "/_auth/logout",
        params=params,
        headers={"origin": origin_of(public_base_url)},
        follow_redirects=False,
    )


def origin_of(public_base_url: str) -> str:
    parts = urlsplit(public_base_url)
    return f"{parts.scheme}://{parts.netloc}"


def replay_client(
    site_dir: Path,
    response,
    public_base_url: str = DEFAULT_PUBLIC_BASE_URL,
    auth_client: DummyAuthClient | None = None,
    **overrides: Any,
) -> TestClient:
    """Build a fresh client carrying exactly the cookie a response handed back.

    ``TestClient``'s jar keeps manually injected cookies that do not match the
    response domain, so replaying the returned ``Set-Cookie`` is the only
    faithful way to assert that a session was really cleared.
    """
    client = build_client(site_dir, public_base_url=public_base_url, auth_client=auth_client, **overrides)
    client.cookies.set(SESSION_COOKIE_NAME, extract_cookie_value(response.headers["set-cookie"]))
    return client


def encode_session_cookie(session: dict, age_seconds: int = 0) -> str:
    signer = TimestampSigner(SESSION_SECRET)
    session_data = base64.b64encode(json.dumps(session).encode("utf-8"))
    if age_seconds:
        with patch("time.time", return_value=time.time() - age_seconds):
            return signer.sign(session_data).decode("utf-8")
    return signer.sign(session_data).decode("utf-8")


def decode_session_cookie(cookie_value: str) -> dict:
    signer = TimestampSigner(SESSION_SECRET)
    signed_data = signer.unsign(cookie_value.encode("utf-8"))
    session_data = base64.b64decode(signed_data)
    return json.loads(session_data)


def extract_cookie_value(set_cookie_header: str) -> str:
    return set_cookie_header.split(f"{SESSION_COOKIE_NAME}=", 1)[1].split(";", 1)[0]


def assert_no_protected_content(body: str) -> None:
    for marker in PROTECTED_MARKERS:
        assert marker not in body, f"response leaked protected content marker {marker!r}"
