"""Raw-ASGI regression tests for request-path canonicalisation.

These tests drive the ASGI application directly instead of going through
``httpx``/``TestClient`` because ``httpx`` normalises dot segments in the URL
before the request is ever sent. A real ASGI server (uvicorn) percent-decodes
``%2e%2e`` into ``..`` and hands the decoded value to the app in
``scope["path"]``, which is exactly what these tests reproduce.
"""

from __future__ import annotations

import errno
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import anyio
import pytest

from server.app import (
    canonicalize_request_path,
    is_existing_directory,
    is_existing_file,
)
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


# --- Path segments longer than the filesystem will accept ----------------------
#
# `NAME_MAX` is 255 bytes on every filesystem this runs on. pathlib absorbs
# ENOENT and ENOTDIR from a stat but not ENAMETOOLONG, so a longer segment used
# to raise OSError out of `Path.is_dir()` -- before authorization, on an
# unauthenticated request, which is a 500 and a log line for anyone who can type
# a long URL.

MAX_SEGMENT_BYTES = 255

OVERLONG_SEGMENT_PATHS = [
    f"/{'a' * 256}",
    f"/{'a' * 300}",
    f"/{'a' * 4096}",
    f"/{'a' * 256}/",
    f"/{'a' * 256}/index.html",
    f"/guides/{'a' * 300}/",
    # Public prefixes reach the filesystem without any session at all.
    f"/assets/{'a' * 256}",
    f"/assets/{'a' * 300}.css",
    f"/javascripts/{'a' * 256}.js",
    f"/stylesheets/{'a' * 256}.css",
    f"/.well-known/{'a' * 300}",
    # The limit is bytes, not characters: 128 two-byte characters overflow it
    # while looking half the length.
    f"/{'é' * 128}",
    f"/assets/{'é' * 128}.css",
    f"/{'😀' * 64}",
    # Each segment is measured on its own, so a long path of short segments is
    # fine and a short path with one long segment is not.
    f"/assets/{'a' * 256}/app.css",
]


@pytest.mark.parametrize("path", OVERLONG_SEGMENT_PATHS)
def test_an_overlong_segment_is_rejected_before_any_filesystem_probe(
    site_dir: Path, path: str
) -> None:
    assert canonicalize_request_path(path) is None


@pytest.mark.parametrize("path", OVERLONG_SEGMENT_PATHS)
def test_an_overlong_segment_answers_not_found_anonymously(site_dir: Path, path: str) -> None:
    app = build_app(site_dir)

    # `raw_path` is percent-encoded ASCII, as a real server delivers it, while
    # `path` carries the decoded characters the app actually screens.
    response = send_raw_request(app, path, raw_path=quote(path))

    assert response.status_code == 404
    assert_no_protected_content(response.text)


@pytest.mark.parametrize("path", OVERLONG_SEGMENT_PATHS)
def test_an_overlong_segment_answers_not_found_when_authenticated(
    site_dir: Path, path: str
) -> None:
    app = build_app(site_dir)

    response = send_raw_request(
        app, path, raw_path=quote(path), session={"user": {"sub": "user-123"}}
    )

    assert response.status_code == 404
    assert_no_protected_content(response.text)


@pytest.mark.parametrize(
    "segment",
    ["a" * MAX_SEGMENT_BYTES, "é" * (MAX_SEGMENT_BYTES // 2), "😀" * (MAX_SEGMENT_BYTES // 4)],
)
def test_a_segment_at_the_limit_is_still_an_ordinary_path(segment: str) -> None:
    """255 bytes is legal, so it has to keep flowing through the normal route."""
    assert canonicalize_request_path(f"/{segment}") == f"/{segment}"


def test_a_missing_protected_path_at_the_limit_reveals_nothing_new(site_dir: Path) -> None:
    """A legal-length miss must answer like every other protected miss.

    Rejecting it as malformed instead would distinguish it from a name that
    could exist, which is a disclosure the length check must not introduce.
    """
    app = build_app(site_dir, auth_client=DummyAuthClient())
    at_limit = f"/{'a' * MAX_SEGMENT_BYTES}"

    response = send_raw_request(app, at_limit)
    ordinary = send_raw_request(app, "/no-such-page")

    assert response.status_code == ordinary.status_code == 307
    assert response.headers["location"].startswith("/_auth/login?next=")
    assert ordinary.headers["location"].startswith("/_auth/login?next=")


def test_a_public_prefix_miss_at_the_limit_is_a_plain_not_found(site_dir: Path) -> None:
    app = build_app(site_dir)

    response = send_raw_request(app, f"/assets/{'a' * MAX_SEGMENT_BYTES}.css")

    assert response.status_code == 404


def test_a_path_of_many_legal_segments_is_not_rejected_for_its_total_length() -> None:
    """Only per-segment length is a filesystem limit worth pre-screening."""
    path = "/" + "/".join(["segment"] * 200)

    assert canonicalize_request_path(path) == path


def test_a_total_path_the_filesystem_may_refuse_is_not_a_fault(site_dir: Path) -> None:
    """The belt to the segment screen's braces.

    `PATH_MAX` bounds the whole path, not just one component, so a stat can
    still be refused for a path of entirely legal segments. Signed in, so the
    answer is the same 404 whether the kernel refuses the probe or simply finds
    nothing.
    """
    app = build_app(site_dir)

    response = send_raw_request(
        app, "/" + "/".join(["segment"] * 2000), session={"user": {"sub": "user-123"}}
    )

    assert response.status_code == 404
    assert_no_protected_content(response.text)


@pytest.mark.parametrize("errno_value", [errno.ENAMETOOLONG, errno.EACCES, errno.EIO])
def test_a_refused_probe_reads_as_absence(errno_value: int) -> None:
    """Pinned independently of which errnos the running pathlib absorbs.

    On the runtime image's CPython, `Path.is_dir` raises ENAMETOOLONG rather
    than returning False, and that call sits ahead of the authorization
    decision. A newer CPython delegates to `os.path.isdir`, which swallows it.
    Neither is a property to depend on.
    """

    class RefusingPath:
        def is_file(self) -> bool:
            raise OSError(errno_value, "refused")

        def is_dir(self) -> bool:
            raise OSError(errno_value, "refused")

    assert is_existing_file(RefusingPath()) is False
    assert is_existing_directory(RefusingPath()) is False
