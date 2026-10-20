"""The absolute ceiling on how long one sign-in lasts.

The session cookie's `max_age` is an *idle* timeout, and Starlette re-issues the
cookie on every response that carries a session, so its clock restarts with
every page view. A tab left open and occasionally clicked therefore never
expires, which makes the eight hours the cookie advertises a fiction.

So the callback stamps the session with when it authenticated, and every
protected request measures against that stamp instead. `max_age` stays: it is
what makes an abandoned browser forget the session, and what stops a cookie
being replayed a month later. The two are different questions and both are
asked.

A missing, malformed, or future stamp is not a session. Sessions predate this
change, an attacker controls what a replayed cookie contains, and neither is a
reason to let a request through.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from server.app import (
    ABSOLUTE_SESSION_LIFETIME_SECONDS,
    SESSION_AUTHENTICATED_AT_KEY,
    SESSION_MAX_AGE_SECONDS,
    SESSION_USER_KEY,
)
from server.tests.support import (
    SESSION_COOKIE_NAME,
    DummyAuthClient,
    assert_no_protected_content,
    build_client,
    decode_session_cookie,
    encode_session_cookie,
    extract_cookie_value,
    replay_client,
    sign_out,
    signed_in_session,
)

NOW = 1_800_000_000
LOGIN_REDIRECT = "/_auth/login?next=%2F"

# A page and a file, because the gate has to hold for both: one resolves
# through the directory-index path, the other straight to a file on disk.
PROTECTED_TARGETS = ("/", "/guides/sso-integration-guide/", "/search/search_index.json")


class FrozenClock:
    """A clock the test moves, so no test has to wait eight hours."""

    def __init__(self, now: float = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def client_signed_in_at(site_dir: Path, authenticated_at: object, clock: FrozenClock, **kwargs):
    client = build_client(site_dir, clock=clock, **kwargs)
    client.cookies.set(
        SESSION_COOKIE_NAME,
        encode_session_cookie(signed_in_session(authenticated_at=authenticated_at)),
    )
    return client


# --- The boundary -------------------------------------------------------------


@pytest.mark.parametrize("path", PROTECTED_TARGETS)
def test_a_fresh_session_reads_protected_content(site_dir: Path, path: str) -> None:
    clock = FrozenClock()
    client = client_signed_in_at(site_dir, NOW, clock)

    assert client.get(path, follow_redirects=False).status_code == 200


def test_a_session_one_second_short_of_the_limit_still_works(site_dir: Path) -> None:
    clock = FrozenClock()
    client = client_signed_in_at(site_dir, NOW, clock)
    clock.advance(ABSOLUTE_SESSION_LIFETIME_SECONDS - 1)

    assert client.get("/", follow_redirects=False).status_code == 200


def test_a_session_exactly_at_the_limit_is_over(site_dir: Path) -> None:
    """Eight hours means eight hours, not eight hours and this request."""
    clock = FrozenClock()
    client = client_signed_in_at(site_dir, NOW, clock)
    clock.advance(ABSOLUTE_SESSION_LIFETIME_SECONDS)

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == LOGIN_REDIRECT


@pytest.mark.parametrize("path", PROTECTED_TARGETS)
def test_nothing_protected_is_readable_past_the_limit(site_dir: Path, path: str) -> None:
    clock = FrozenClock()
    client = client_signed_in_at(site_dir, NOW, clock)
    clock.advance(ABSOLUTE_SESSION_LIFETIME_SECONDS + 1)

    response = client.get(path, follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"].startswith("/_auth/login?next=")
    assert_no_protected_content(response.text)


def test_using_the_session_all_day_does_not_extend_it(site_dir: Path) -> None:
    """The failure this change exists for: activity used to renew the cookie.

    Starlette re-signs the cookie on every response, so the idle clock kept
    restarting. The stamp inside the session does not move, so the ceiling
    arrives on schedule however busy the tab was.
    """
    clock = FrozenClock()
    client = client_signed_in_at(site_dir, NOW, clock)

    for _ in range(8):
        assert client.get("/", follow_redirects=False).status_code == 200
        clock.advance(3600 - 1)

    assert client.get("/", follow_redirects=False).status_code == 200

    clock.advance(8)
    assert client.get("/", follow_redirects=False).status_code == 307


def test_the_reissued_cookie_keeps_the_original_timestamp(site_dir: Path) -> None:
    """Otherwise every request would quietly restart the eight hours.

    Starlette re-signs the cookie on each response, which is what refreshes the
    idle expiry; the stamp inside it has to survive that untouched.
    """
    clock = FrozenClock()
    client = client_signed_in_at(site_dir, NOW, clock)
    clock.advance(3600)

    response = client.get("/", follow_redirects=False)

    session = decode_session_cookie(extract_cookie_value(response.headers["set-cookie"]))
    assert session[SESSION_AUTHENTICATED_AT_KEY] == NOW


# --- Anything that is not a timestamp -----------------------------------------


def test_a_session_with_no_timestamp_fails_closed(site_dir: Path) -> None:
    """Every session signed before this change looks like this."""
    clock = FrozenClock()
    client = build_client(site_dir, clock=clock)
    client.cookies.set(
        SESSION_COOKIE_NAME, encode_session_cookie({SESSION_USER_KEY: {"sub": "user-123"}})
    )

    assert client.get("/", follow_redirects=False).status_code == 307


@pytest.mark.parametrize(
    "stamp",
    [None, "1800000000", "", True, {"at": NOW}, [NOW], float("nan"), float("inf")],
    ids=["null", "string", "empty", "boolean", "object", "list", "nan", "infinity"],
)
def test_a_malformed_timestamp_fails_closed(site_dir: Path, stamp: object) -> None:
    """A forged cookie chooses this value, so nothing here may be trusted.

    `nan` matters on its own: every comparison against it is false, so an age
    check written the obvious way would let it through.
    """
    clock = FrozenClock()
    client = client_signed_in_at(site_dir, stamp, clock)

    assert client.get("/", follow_redirects=False).status_code == 307


def test_a_timestamp_in_the_future_fails_closed(site_dir: Path) -> None:
    """A session cannot have started after now; a forged one can claim to."""
    clock = FrozenClock()
    client = client_signed_in_at(site_dir, NOW + 60, clock)

    assert client.get("/", follow_redirects=False).status_code == 307


def test_a_replayed_cookie_from_yesterday_is_refused(site_dir: Path) -> None:
    """Signed correctly, replayed later: the stamp is inside the signature."""
    clock = FrozenClock()
    client = client_signed_in_at(site_dir, NOW - 24 * 3600, clock)

    assert client.get("/", follow_redirects=False).status_code == 307


def test_a_session_with_no_subject_fails_closed(site_dir: Path) -> None:
    """The user object is forgeable too, so it is checked, not assumed."""
    clock = FrozenClock()
    client = build_client(site_dir, clock=clock)
    client.cookies.set(
        SESSION_COOKIE_NAME,
        encode_session_cookie({SESSION_USER_KEY: {}, SESSION_AUTHENTICATED_AT_KEY: NOW}),
    )

    assert client.get("/", follow_redirects=False).status_code == 307


# --- An expired session is ended, not just refused ----------------------------


def test_an_expired_session_is_cleared_from_the_cookie(site_dir: Path) -> None:
    """Refusing the request but keeping the cookie leaves a zombie session."""
    clock = FrozenClock()
    client = client_signed_in_at(site_dir, NOW, clock)
    clock.advance(ABSOLUTE_SESSION_LIFETIME_SECONDS)

    response = client.get("/", follow_redirects=False)

    # Nothing is left in the session, so Starlette deletes the cookie outright
    # rather than re-signing an empty one.
    set_cookie = response.headers["set-cookie"]
    assert extract_cookie_value(set_cookie) == "null"
    assert "expires=Thu, 01 Jan 1970" in set_cookie

    replayed = replay_client(site_dir, response, clock=clock)
    assert replayed.get("/", follow_redirects=False).status_code == 307


def test_an_expired_session_does_not_get_treated_as_signed_in_by_logout(
    site_dir: Path,
) -> None:
    """Otherwise a stale cookie drives an outbound request to the issuer.

    The anonymous case is already kept local so nobody can pump metadata
    requests in a loop; an expired session is the same caller.
    """
    auth_client = DummyAuthClient(metadata={"end_session_endpoint": "https://issuer/logout"})
    clock = FrozenClock()
    client = client_signed_in_at(site_dir, NOW, clock, auth_client=auth_client)
    clock.advance(ABSOLUTE_SESSION_LIFETIME_SECONDS)

    response = sign_out(client)

    assert response.headers["location"] == "/logged-off"
    assert auth_client.metadata_requests == 0


def test_a_live_session_still_reaches_the_issuer_on_logout(site_dir: Path) -> None:
    """The contrast that makes the test above mean something."""
    auth_client = DummyAuthClient(metadata={"end_session_endpoint": "https://issuer/logout"})
    clock = FrozenClock()
    client = client_signed_in_at(site_dir, NOW, clock, auth_client=auth_client)

    response = sign_out(client)

    assert response.headers["location"].startswith("https://issuer/logout")
    assert auth_client.metadata_requests == 1


# --- Public pages are not affected --------------------------------------------


def test_an_expired_session_still_reads_public_pages(site_dir: Path) -> None:
    clock = FrozenClock()
    client = client_signed_in_at(site_dir, NOW, clock)
    clock.advance(ABSOLUTE_SESSION_LIFETIME_SECONDS)

    assert client.get("/privacy-policy/", follow_redirects=False).status_code == 200


def test_a_public_page_does_not_examine_the_session_at_all(site_dir: Path) -> None:
    """Nothing about a public answer depends on who is asking.

    So the check runs where the answer depends on it -- protected paths and
    logout -- and an expired cookie is ended on the first protected request
    rather than on any request at all. The response stays cacheable, and the
    session it hands back is the one it was given.
    """
    clock = FrozenClock()
    client = client_signed_in_at(site_dir, NOW, clock)
    clock.advance(ABSOLUTE_SESSION_LIFETIME_SECONDS)

    response = client.get("/privacy-policy/", follow_redirects=False)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=300"
    handed_back = decode_session_cookie(extract_cookie_value(response.headers["set-cookie"]))
    assert handed_back == signed_in_session(authenticated_at=NOW)


# --- The callback is what stamps it -------------------------------------------


def test_the_callback_records_when_it_authenticated(site_dir: Path) -> None:
    clock = FrozenClock()
    client = build_client(site_dir, clock=clock, auth_client=DummyAuthClient())
    client.cookies.set(
        SESSION_COOKIE_NAME, encode_session_cookie({"pending_logins": {"st": "/"}})
    )

    response = client.get("/_auth/callback?code=c&state=st", follow_redirects=False)

    session = decode_session_cookie(extract_cookie_value(response.headers["set-cookie"]))
    assert session[SESSION_AUTHENTICATED_AT_KEY] == NOW


def test_signing_in_again_starts_a_new_eight_hours(site_dir: Path) -> None:
    """An expired session is not a dead end: the tab's pending login still works.

    Re-authenticating is a new authentication, so it gets a new ceiling. What
    it must not do is inherit the old stamp, or a user could be locked out of
    signing in for eight hours after their session lapsed.
    """
    clock = FrozenClock()
    client = build_client(site_dir, clock=clock, auth_client=DummyAuthClient())
    client.cookies.set(
        SESSION_COOKIE_NAME,
        encode_session_cookie(
            {
                **signed_in_session(authenticated_at=NOW),
                "pending_logins": {"st": "/security/"},
            }
        ),
    )
    clock.advance(ABSOLUTE_SESSION_LIFETIME_SECONDS + 1)
    assert client.get("/", follow_redirects=False).status_code == 307

    response = client.get("/_auth/callback?code=c&state=st", follow_redirects=False)

    assert response.status_code == 307
    session = decode_session_cookie(extract_cookie_value(response.headers["set-cookie"]))
    assert session[SESSION_AUTHENTICATED_AT_KEY] == clock.now
    resumed = replay_client(site_dir, response, clock=clock)
    assert resumed.get("/", follow_redirects=False).status_code == 200


def test_the_session_holds_the_timestamp_and_nothing_else_new(site_dir: Path) -> None:
    """The cookie budget is tight; a lifetime costs one integer."""
    clock = FrozenClock()
    client = build_client(site_dir, clock=clock, auth_client=DummyAuthClient())
    client.cookies.set(
        SESSION_COOKIE_NAME, encode_session_cookie({"pending_logins": {"st": "/"}})
    )

    response = client.get("/_auth/callback?code=c&state=st", follow_redirects=False)

    session = decode_session_cookie(extract_cookie_value(response.headers["set-cookie"]))
    assert set(session) == {SESSION_USER_KEY, SESSION_AUTHENTICATED_AT_KEY}
    assert isinstance(session[SESSION_AUTHENTICATED_AT_KEY], int)


# --- The idle timeout is still there ------------------------------------------


def test_the_cookie_still_carries_an_idle_expiry(site_dir: Path) -> None:
    """`max_age` answers a different question and is still asked."""
    clock = FrozenClock()
    client = client_signed_in_at(site_dir, NOW, clock)

    response = client.get("/", follow_redirects=False)

    assert f"max-age={SESSION_MAX_AGE_SECONDS}" in response.headers["set-cookie"].lower()


def test_an_idle_cookie_older_than_max_age_is_refused_by_the_signature(
    site_dir: Path,
) -> None:
    """The two limits are independent: this one never reaches the app's check."""
    clock = FrozenClock()
    client = build_client(site_dir, clock=clock)
    client.cookies.set(
        SESSION_COOKIE_NAME,
        encode_session_cookie(
            signed_in_session(authenticated_at=NOW), age_seconds=SESSION_MAX_AGE_SECONDS + 60
        ),
    )

    assert client.get("/", follow_redirects=False).status_code == 307


def test_the_two_limits_are_configured_separately(site_dir: Path) -> None:
    """Equal today, and not the same setting: one is the cookie, one the session."""
    clock = FrozenClock()
    client = build_client(site_dir, clock=clock, session_max_age_seconds=60)
    client.cookies.set(
        SESSION_COOKIE_NAME, encode_session_cookie(signed_in_session(authenticated_at=NOW))
    )

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 200
    assert "max-age=60" in response.headers["set-cookie"].lower()
    assert ABSOLUTE_SESSION_LIFETIME_SECONDS == 8 * 60 * 60
