"""End-to-end regression smoke against a real uvicorn socket.

``httpx`` (and therefore ``TestClient``) rewrites literal ``..`` segments and
collapses encoded ones before sending, so an encoded-traversal bypass is
invisible to it. These tests speak HTTP/1.1 over a raw socket against a real
uvicorn server so the request line reaches the ASGI app exactly as written.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import uvicorn

from server.tests.support import (
    assert_no_protected_content,
    build_app,
    encode_session_cookie,
    write_site,
)

STARTUP_TIMEOUT_SECONDS = 20.0
SHUTDOWN_TIMEOUT_SECONDS = 20.0


@dataclass(frozen=True)
class RawHttpResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


@pytest.fixture(scope="module")
def live_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[int]:
    site_dir = write_site(tmp_path_factory.mktemp("live-site"))
    app = build_app(site_dir, public_base_url="http://127.0.0.1")
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            server.should_exit = True
            raise TimeoutError("uvicorn did not start in time")
        time.sleep(0.05)

    try:
        yield server.servers[0].sockets[0].getsockname()[1]
    finally:
        server.should_exit = True
        thread.join(timeout=SHUTDOWN_TIMEOUT_SECONDS)


def raw_http_get(port: int, target: str, cookie: str | None = None) -> RawHttpResponse:
    request_lines = [
        f"GET {target} HTTP/1.1",
        f"Host: 127.0.0.1:{port}",
        "Connection: close",
    ]
    if cookie is not None:
        request_lines.append(f"Cookie: authifi-session={cookie}")
    request = ("\r\n".join(request_lines) + "\r\n\r\n").encode("latin-1")

    chunks: list[bytes] = []
    with socket.create_connection(("127.0.0.1", port), timeout=10) as connection:
        connection.sendall(request)
        while True:
            chunk = connection.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)

    head, _, body = b"".join(chunks).partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    status_code = int(lines[0].split(" ")[1])
    headers: dict[str, str] = {}
    for line in lines[1:]:
        name, separator, value = line.partition(":")
        if separator:
            headers.setdefault(name.strip().lower(), value.strip())
    return RawHttpResponse(status_code=status_code, headers=headers, body=body)


ENCODED_BYPASS_TARGETS = [
    "/assets/%2e%2e/index.html",
    "/assets/%2E%2E/index.html",
    "/assets/%2e%2e/search/search_index.json",
    "/assets/%2e%2e/guides/sso-integration-guide/index.html",
    "/javascripts/%2e%2e/index.html",
    "/stylesheets/%2e%2e/search/search_index.json",
    "/.well-known/%2e%2e/index.html",
    "/assets/%2e%2e%2findex.html",
    "/assets/../index.html",
]


@pytest.mark.parametrize("target", ENCODED_BYPASS_TARGETS)
def test_anonymous_encoded_traversal_returns_no_protected_content(live_server: int, target: str) -> None:
    response = raw_http_get(live_server, target)

    assert response.status_code == 404
    assert_no_protected_content(response.text)


@pytest.mark.parametrize("target", ENCODED_BYPASS_TARGETS)
def test_authenticated_encoded_traversal_is_still_rejected(live_server: int, target: str) -> None:
    cookie = encode_session_cookie({"user": {"sub": "user-123"}})

    response = raw_http_get(live_server, target, cookie=cookie)

    assert response.status_code == 404
    assert_no_protected_content(response.text)


@pytest.mark.parametrize(
    ("target", "expected_content_type"),
    [
        ("/privacy-policy/", "text/html; charset=utf-8"),
        ("/terms-of-service/", "text/html; charset=utf-8"),
        ("/sms-opt-in.html", "text/html; charset=utf-8"),
        ("/assets/app.css", "text/css; charset=utf-8"),
        ("/javascripts/app.js", "text/javascript; charset=utf-8"),
        ("/robots.txt", "text/plain; charset=utf-8"),
        ("/sitemap.xml", "application/xml"),
        ("/auth.md", "text/markdown; charset=utf-8"),
        ("/.well-known/agent-skills/index.json", "application/json"),
    ],
)
def test_public_paths_serve_correct_content_types(
    live_server: int, target: str, expected_content_type: str
) -> None:
    response = raw_http_get(live_server, target)

    assert response.status_code == 200
    assert response.headers["content-type"] == expected_content_type


def test_protected_html_is_served_as_html_after_login(live_server: int) -> None:
    cookie = encode_session_cookie({"user": {"sub": "user-123"}})

    response = raw_http_get(live_server, "/", cookie=cookie)

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert "Private home" in response.text


def test_anonymous_root_redirects_to_login_with_no_store(live_server: int) -> None:
    response = raw_http_get(live_server, "/")

    assert response.status_code == 307
    assert response.headers["location"] == "/_auth/login?next=%2F"
    assert response.headers["cache-control"] == "private, no-store"
    assert_no_protected_content(response.text)


def test_responses_carry_baseline_security_headers(live_server: int) -> None:
    response = raw_http_get(live_server, "/privacy-policy/")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "strict-transport-security" not in response.headers


def test_public_directory_route_canonicalises_without_login(live_server: int) -> None:
    response = raw_http_get(live_server, "/privacy-policy")

    assert response.status_code == 308
    assert response.headers["location"] == "/privacy-policy/"


def test_anonymous_protected_directory_route_reveals_nothing_over_the_wire(live_server: int) -> None:
    existing = raw_http_get(live_server, "/guides/sso-integration-guide")
    missing = raw_http_get(live_server, "/guides/no-such-guide")

    assert existing.status_code == 307
    assert existing.headers["location"] == "/_auth/login?next=%2Fguides%2Fsso-integration-guide"
    assert missing.status_code == 307
    assert missing.headers["location"] == "/_auth/login?next=%2Fguides%2Fno-such-guide"
    assert_no_protected_content(existing.text)


def test_authenticated_protected_directory_route_canonicalises(live_server: int) -> None:
    cookie = encode_session_cookie({"user": {"sub": "user-123"}})

    response = raw_http_get(live_server, "/guides/sso-integration-guide", cookie=cookie)

    assert response.status_code == 308
    assert response.headers["location"] == "/guides/sso-integration-guide/"
    assert_no_protected_content(response.text)


def test_file_response_exposes_validators_for_conditional_requests(live_server: int) -> None:
    response = raw_http_get(live_server, "/assets/app.css")

    assert response.status_code == 200
    assert "etag" in response.headers
    assert "last-modified" in response.headers
    assert response.headers["accept-ranges"] == "bytes"
