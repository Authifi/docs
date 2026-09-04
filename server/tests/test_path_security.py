"""Raw-ASGI regression tests for request-path canonicalisation.

These tests drive the ASGI application directly instead of going through
``httpx``/``TestClient`` because ``httpx`` normalises dot segments in the URL
before the request is ever sent. A real ASGI server (uvicorn) percent-decodes
``%2e%2e`` into ``..`` and hands the decoded value to the app in
``scope["path"]``, which is exactly what these tests reproduce.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import anyio
import pytest

from server.app import canonicalize_request_path
from server.tests.support import (
    DummyAuthClient,
    assert_no_protected_content,
    build_app,
    encode_session_cookie,
)


@dataclass(frozen=True)
class RawResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


def send_raw_request(
    app,
    path: str,
    raw_path: str | None = None,
    query_string: bytes = b"",
    session: dict | None = None,
) -> RawResponse:
    """Invoke the ASGI app with a fully controlled, already-decoded path."""
    headers: list[tuple[bytes, bytes]] = [(b"host", b"testserver")]
    if session is not None:
        cookie = f"authifi-session={encode_session_cookie(session)}"
        headers.append((b"cookie", cookie.encode("latin-1")))

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": (raw_path if raw_path is not None else path).encode("latin-1"),
        "root_path": "",
        "query_string": query_string,
        "headers": headers,
        "client": ("127.0.0.1", 54321),
        "server": ("testserver", 80),
    }

    messages: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        messages.append(message)

    anyio.run(app, scope, receive, send)

    start = next(message for message in messages if message["type"] == "http.response.start")
    body = b"".join(
        message.get("body", b"") for message in messages if message["type"] == "http.response.body"
    )
    response_headers = {
        key.decode("latin-1").lower(): value.decode("latin-1") for key, value in start["headers"]
    }
    return RawResponse(status_code=start["status"], headers=response_headers, body=body)


# Every public prefix class, plus the site root, expressed with the dot segments
# that a real server produces after percent-decoding.
DECODED_TRAVERSAL_PATHS = [
    "/assets/../index.html",
    "/assets/../search/search_index.json",
    "/assets/../guides/sso-integration-guide/index.html",
    "/assets/../security/index.html",
    "/assets/./../index.html",
    "/assets/../assets/../index.html",
    "/javascripts/../index.html",
    "/javascripts/../search/search_index.json",
    "/stylesheets/../index.html",
    "/stylesheets/../search/search_index.json",
    "/.well-known/../index.html",
    "/.well-known/../search/search_index.json",
    "/.well-known/../guides/sso-integration-guide/index.html",
    "/assets/..",
    "/assets/../",
    "/./index.html",
    "/../index.html",
    "/assets//../index.html",
]


@pytest.mark.parametrize("path", DECODED_TRAVERSAL_PATHS)
def test_dot_segment_paths_never_serve_protected_content_anonymously(site_dir: Path, path: str) -> None:
    app = build_app(site_dir)

    response = send_raw_request(app, path)

    assert response.status_code == 404
    assert_no_protected_content(response.text)


@pytest.mark.parametrize("path", DECODED_TRAVERSAL_PATHS)
def test_dot_segment_paths_are_rejected_even_when_authenticated(site_dir: Path, path: str) -> None:
    app = build_app(site_dir)

    response = send_raw_request(app, path, session={"user": {"sub": "user-123"}})

    assert response.status_code == 404
    assert_no_protected_content(response.text)


def test_encoded_traversal_to_search_index_does_not_leak_the_index(site_dir: Path) -> None:
    app = build_app(site_dir)

    response = send_raw_request(
        app,
        "/assets/../search/search_index.json",
        raw_path="/assets/%2e%2e/search/search_index.json",
    )

    assert response.status_code == 404
    assert "protected-search-entry" not in response.text


@pytest.mark.parametrize(
    "path",
    [
        "/assets\\..\\index.html",
        "/assets/\\/../index.html",
        "/index.html\x00.css",
        "/assets/app.css\n",
        "//index.html",
        "/assets//app.css",
    ],
)
def test_backslash_control_and_empty_segment_paths_are_rejected(site_dir: Path, path: str) -> None:
    app = build_app(site_dir)

    response = send_raw_request(app, path)

    assert response.status_code == 404
    assert_no_protected_content(response.text)


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/privacy-policy/",
        "/assets/app.css",
        "/.well-known/api-catalog",
        "/search/search_index.json",
        "/guides/sso-integration-guide/",
    ],
)
def test_canonicalize_request_path_accepts_ordinary_paths(path: str) -> None:
    assert canonicalize_request_path(path) == path


@pytest.mark.parametrize(
    "path",
    [
        "/assets/../index.html",
        "/./index.html",
        "/..",
        "/.",
        "//double",
        "/back\\slash",
        "/nul\x00byte",
        "relative/path",
    ],
)
def test_canonicalize_request_path_rejects_unsafe_paths(path: str) -> None:
    assert canonicalize_request_path(path) is None


def test_public_prefix_dot_segment_does_not_bypass_authorization_for_valid_target(
    site_dir: Path,
) -> None:
    """The traversal target exists and is protected; it must never be served."""
    app = build_app(site_dir, auth_client=DummyAuthClient())

    bypass = send_raw_request(app, "/assets/../guides/sso-integration-guide/index.html")
    direct = send_raw_request(app, "/guides/sso-integration-guide/index.html")

    assert bypass.status_code == 404
    assert direct.status_code == 307
    assert direct.headers["location"].startswith("/_auth/login?next=")
    assert_no_protected_content(bypass.text)
    assert_no_protected_content(direct.text)
