"""Logout is a state change, so it is a POST and it checks where it came from.

`GET /_auth/logout` ended the session and, when the tenant published one,
redirected through the issuer's end-session endpoint. Any page anywhere could
therefore sign a reader out with an `<img src="https://docs.authifi.io/_auth/
logout">`, and a prefetcher or link scanner could do it without anyone
involved. Neither is a security breach -- there is nothing to steal by logging
somebody out -- but it makes the site unusable while it is happening, and a
GET that changes state is not something to leave in place.

So the route is POST-only, and every POST has to carry an `Origin` matching the
one `PUBLIC_BASE_URL` names. No token: the pages are static files, so there is
nowhere to mint one per render, and the site would need a cookie or an endpoint
to hand tokens out. `SameSite=Lax` already keeps the session cookie off a
cross-site POST, which means a forged one would arrive anonymous; the `Origin`
check is what makes that a refusal rather than a redirect, and what covers the
same-site-but-different-port case `Lax` does not.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.app import DEFAULT_POST_LOGOUT_PATH, create_app, normalize_origin
from server.tests.support import (
    DEFAULT_PUBLIC_BASE_URL,
    DummyAuthClient,
    authenticated_client,
    build_client,
    build_config,
    origin_of,
    replay_client,
    sign_out,
)

OUR_ORIGIN = origin_of(DEFAULT_PUBLIC_BASE_URL)
ISSUER_LOGOUT = "https://issuer.example.com/oidc/logout"


def discovering_client() -> DummyAuthClient:
    return DummyAuthClient(metadata={"end_session_endpoint": ISSUER_LOGOUT})


def still_signed_in(site_dir: Path, response) -> bool:
    """Whether the session survived, judged by the cookie the response returned."""
    if "set-cookie" not in response.headers:
        return True
    replayed = replay_client(site_dir, response).get("/", follow_redirects=False)
    return replayed.status_code == 200


# --- GET no longer does anything ----------------------------------------------


def test_a_get_is_refused_with_the_method_that_works(site_dir: Path) -> None:
    auth_client = discovering_client()
    client = authenticated_client(site_dir, auth_client=auth_client)

    response = client.get("/_auth/logout", follow_redirects=False)

    assert response.status_code == 405
    assert "POST" in response.headers["allow"]
    assert auth_client.metadata_requests == 0


def test_a_get_leaves_the_session_signed_in(site_dir: Path) -> None:
    """The whole point: a prefetch or an `<img>` tag must change nothing."""
    client = authenticated_client(site_dir, auth_client=discovering_client())

    response = client.get("/_auth/logout", follow_redirects=False)

    assert still_signed_in(site_dir, response)
    assert client.get("/", follow_redirects=False).status_code == 200


def test_a_head_is_refused_too(site_dir: Path) -> None:
    client = authenticated_client(site_dir, auth_client=discovering_client())

    assert client.head("/_auth/logout", follow_redirects=False).status_code == 405


def test_the_refusal_of_a_get_is_not_cacheable(site_dir: Path) -> None:
    client = authenticated_client(site_dir)

    response = client.get("/_auth/logout", follow_redirects=False)

    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


# --- POST from our own origin works -------------------------------------------


def test_a_post_from_the_sites_own_origin_signs_out(site_dir: Path) -> None:
    auth_client = discovering_client()
    client = authenticated_client(site_dir, auth_client=auth_client)

    response = sign_out(client)

    assert response.status_code == 307
    assert response.headers["location"].startswith(ISSUER_LOGOUT)
    assert auth_client.metadata_requests == 1
    assert not still_signed_in(site_dir, response)


def test_an_anonymous_post_from_our_origin_still_redirects_locally(site_dir: Path) -> None:
    auth_client = discovering_client()
    client = build_client(site_dir, auth_client=auth_client)

    response = sign_out(client)

    assert response.status_code == 307
    assert response.headers["location"] == DEFAULT_POST_LOGOUT_PATH
    assert auth_client.metadata_requests == 0


def test_the_expected_origin_comes_from_the_public_base_url(site_dir: Path) -> None:
    """A local stack and production do not share an origin."""
    local = "http://localhost:8000"
    client = authenticated_client(site_dir, public_base_url=local)

    assert sign_out(client, public_base_url=local).status_code == 307
    assert sign_out(client, public_base_url=DEFAULT_PUBLIC_BASE_URL).status_code == 403


@pytest.mark.parametrize(
    "origin",
    [OUR_ORIGIN, f"{OUR_ORIGIN}:443", "https://DOCS.example.com"],
    ids=["exact", "with-the-default-port", "different-case"],
)
def test_the_origin_is_compared_as_an_origin_not_as_a_string(site_dir: Path, origin: str) -> None:
    """`https://host` and `https://host:443` are the same origin, and hosts are
    case-insensitive. Comparing the header verbatim would refuse both."""
    client = authenticated_client(site_dir)

    response = client.post(
        "/_auth/logout", headers={"origin": origin}, follow_redirects=False
    )

    assert response.status_code == 307


# --- POST from anywhere else is refused ---------------------------------------

FOREIGN_ORIGINS = {
    "attacker": "https://attacker.example",
    "downgraded-scheme": "http://docs.example.com",
    "another-port": "https://docs.example.com:8443",
    "suffix-of-our-host": "https://evil-docs.example.com",
    "our-host-as-a-prefix": "https://docs.example.com.evil.test",
    "subdomain": "https://www.docs.example.com",
    "opaque": "null",
    "path-only": "/",
    "not-a-url": "definitely not an origin",
    "with-a-path": "https://docs.example.com/_auth/logout",
    "with-credentials": "https://user:pass@docs.example.com",
    "unparseable-port": "https://docs.example.com:notaport",
    "empty": "",
}


@pytest.mark.parametrize("origin", FOREIGN_ORIGINS.values(), ids=FOREIGN_ORIGINS)
def test_a_post_from_a_foreign_or_malformed_origin_is_refused(
    site_dir: Path, origin: str
) -> None:
    auth_client = discovering_client()
    client = authenticated_client(site_dir, auth_client=auth_client)

    response = client.post(
        "/_auth/logout", headers={"origin": origin}, follow_redirects=False
    )

    assert response.status_code == 403
    assert auth_client.metadata_requests == 0
    assert still_signed_in(site_dir, response)


def test_a_post_with_no_origin_at_all_is_refused(site_dir: Path) -> None:
    """Fail closed. Every browser this site supports sends `Origin` on a form
    POST, so a request without one is not a form submission from our pages."""
    auth_client = discovering_client()
    client = authenticated_client(site_dir, auth_client=auth_client)

    response = client.post("/_auth/logout", follow_redirects=False)

    assert response.status_code == 403
    assert auth_client.metadata_requests == 0
    assert still_signed_in(site_dir, response)


def test_a_refused_post_leaves_the_session_usable(site_dir: Path) -> None:
    """Not merely uncleared: the reader carries on as if nothing happened."""
    client = authenticated_client(site_dir, auth_client=discovering_client())

    client.post("/_auth/logout", headers={"origin": "https://attacker.example"})

    assert client.get("/", follow_redirects=False).status_code == 200


def test_a_refused_post_does_not_echo_what_it_was_sent(site_dir: Path) -> None:
    client = authenticated_client(site_dir)

    response = client.post(
        "/_auth/logout",
        headers={"origin": "https://attacker.example"},
        follow_redirects=False,
    )

    assert "attacker.example" not in response.text


def test_the_refusal_is_not_cacheable(site_dir: Path) -> None:
    client = authenticated_client(site_dir)

    response = client.post(
        "/_auth/logout",
        headers={"origin": "https://attacker.example"},
        follow_redirects=False,
    )

    assert response.headers["cache-control"] == "private, no-store"


def test_a_refused_post_is_logged_without_the_header_it_refused(
    site_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The header is attacker-controlled and unbounded; the log records the shape."""
    client = authenticated_client(site_dir)

    with caplog.at_level("WARNING"):
        client.post(
            "/_auth/logout",
            headers={"origin": "https://attacker.example/" + "x" * 4000},
            follow_redirects=False,
        )

    assert "attacker.example" not in caplog.text
    assert "origin" in caplog.text.lower()


# --- Parsing an origin --------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("https://docs.example.com", "https://docs.example.com"),
        ("https://docs.example.com:443", "https://docs.example.com"),
        ("http://localhost:80", "http://localhost"),
        ("http://localhost:8000", "http://localhost:8000"),
        ("https://DOCS.Example.COM", "https://docs.example.com"),
        ("HTTPS://docs.example.com", "https://docs.example.com"),
    ],
)
def test_an_origin_is_reduced_to_its_canonical_form(value: str, expected: str) -> None:
    assert normalize_origin(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "null",
        "/",
        "docs.example.com",
        "ftp://docs.example.com",
        "file://",
        "https://",
        "https://docs.example.com/path",
        "https://docs.example.com?query",
        "https://docs.example.com#fragment",
        "https://docs.example.com:notaport",
        "https://docs.example.com\nOrigin: https://attacker.example",
    ],
)
def test_anything_that_is_not_an_origin_parses_to_nothing(value: str | None) -> None:
    assert normalize_origin(value) is None


def test_a_public_base_url_with_a_path_still_has_an_origin() -> None:
    """The config is ours, and a sub-path deployment is a legitimate shape.

    Only the header is held to the stricter rule, because browsers never send a
    path in one and accepting anything looser widens what counts as same-origin.
    """
    assert normalize_origin("https://docs.example.com/docs/", allow_path=True) == (
        "https://docs.example.com"
    )
    assert normalize_origin("https://docs.example.com/docs/") is None


def test_a_deployment_whose_public_base_url_is_not_an_origin_fails_at_startup(
    site_dir: Path,
) -> None:
    """Otherwise it starts and every logout answers 403 instead."""
    with pytest.raises(ValueError, match="PUBLIC_BASE_URL"):
        create_app(build_config(site_dir, public_base_url="docs.example.com"))

    with pytest.raises(ValueError, match="PUBLIC_BASE_URL"):
        create_app(build_config(site_dir, public_base_url="ftp://docs.example.com"))
