"""Concurrent OAuth logins from one browser session.

Two tabs on two gated pages produce two logins against one cookie. These tests
drive the real Authlib client so its own per-state PKCE and nonce bookkeeping is
exercised alongside ours; only the token endpoint is faked, because there is no
issuer to talk to.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from starlette.testclient import TestClient

from server.app import (
    MAX_PENDING_LOGINS,
    SESSION_PENDING_LOGINS_KEY,
    SESSION_USER_KEY,
    create_app,
    oauth_state_session_key,
)
from server.tests.support import (
    build_config,
    decode_session_cookie,
    encode_session_cookie,
    extract_cookie_value,
    write_site,
)

SESSION_COOKIE_NAME = "authifi-session"


@pytest.fixture
def site_dir(tmp_path: Path) -> Path:
    return write_site(tmp_path / "site")


class RecordingTokenEndpoint:
    """Stands in for the issuer's token endpoint.

    Authlib still does the real work under test: looking up the state entry,
    clearing exactly that one, and passing the matching PKCE verifier through.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, **params):
        self.calls.append(params)
        code = params.get("code")
        return {
            "access_token": f"access-token-for-{code}",
            "userinfo": {"sub": f"user-{code}", "email": f"{code}@example.com"},
        }


@pytest.fixture
def token_endpoint() -> RecordingTokenEndpoint:
    return RecordingTokenEndpoint()


@pytest.fixture
def client(site_dir: Path, token_endpoint: RecordingTokenEndpoint) -> TestClient:
    # Plain http so the session cookie is not marked Secure: these tests depend
    # on the client returning it across several requests, which is the whole
    # point of a shared browser session.
    app = create_app(build_config(site_dir, public_base_url="http://testserver"))

    async def metadata() -> dict[str, str]:
        return {
            "authorization_endpoint": "https://issuer.example.com/oauth2/authorize",
            "token_endpoint": "https://issuer.example.com/oauth2/token",
            "jwks_uri": "https://issuer.example.com/.well-known/jwks.json",
        }

    app.state.auth_client.load_server_metadata = metadata
    app.state.auth_client.fetch_access_token = token_endpoint
    return TestClient(app)


def start_login(client: TestClient, next_path: str) -> str:
    """Begin a login and return the OAuth state Authlib was handed."""
    response = client.get(f"/_auth/login?next={next_path}", follow_redirects=False)

    assert response.status_code == 302
    return parse_qs(urlparse(response.headers["location"]).query)["state"][0]


def current_session(client: TestClient) -> dict:
    cookie = client.cookies.get(SESSION_COOKIE_NAME)
    return decode_session_cookie(cookie) if cookie else {}


def complete_login(client: TestClient, state: str, code: str):
    return client.get(f"/_auth/callback?code={code}&state={state}", follow_redirects=False)


# --- Two tabs, completed out of order -----------------------------------------


def test_two_logins_keep_separate_destinations(client: TestClient) -> None:
    guide_state = start_login(client, "/guides/sso-integration-guide/")
    security_state = start_login(client, "/security/")

    pending = current_session(client)[SESSION_PENDING_LOGINS_KEY]

    assert guide_state != security_state
    assert pending == {
        guide_state: "/guides/sso-integration-guide/",
        security_state: "/security/",
    }


def test_the_second_login_survives_the_first_completing(client: TestClient) -> None:
    """A callback used to `session.clear()`, taking every other tab with it."""
    guide_state = start_login(client, "/guides/sso-integration-guide/")
    security_state = start_login(client, "/security/")

    first = complete_login(client, guide_state, "code-guide")
    assert first.headers["location"] == "/guides/sso-integration-guide/"

    session = current_session(client)
    assert list(session[SESSION_PENDING_LOGINS_KEY]) == [security_state]
    assert oauth_state_session_key(security_state) in session
    assert oauth_state_session_key(guide_state) not in session

    second = complete_login(client, security_state, "code-security")
    assert second.status_code == 307
    assert second.headers["location"] == "/security/"


def test_callbacks_completed_out_of_order_each_land_on_their_own_page(
    client: TestClient,
) -> None:
    guide_state = start_login(client, "/guides/sso-integration-guide/")
    security_state = start_login(client, "/security/")

    newest_first = complete_login(client, security_state, "code-security")
    oldest_second = complete_login(client, guide_state, "code-guide")

    assert newest_first.headers["location"] == "/security/"
    assert oldest_second.headers["location"] == "/guides/sso-integration-guide/"


def test_each_callback_uses_its_own_pkce_verifier(
    client: TestClient, token_endpoint: RecordingTokenEndpoint
) -> None:
    """Proof that Authlib consumed the right state entry, not just any entry."""
    guide_state = start_login(client, "/guides/sso-integration-guide/")
    security_state = start_login(client, "/security/")
    verifiers = {
        state: current_session(client)[oauth_state_session_key(state)]["data"]["code_verifier"]
        for state in (guide_state, security_state)
    }

    complete_login(client, security_state, "code-security")
    complete_login(client, guide_state, "code-guide")

    used = {call["code"]: call["code_verifier"] for call in token_endpoint.calls}
    assert used["code-security"] == verifiers[security_state]
    assert used["code-guide"] == verifiers[guide_state]
    assert used["code-security"] != used["code-guide"]


def test_the_last_completed_login_owns_the_signed_in_user(client: TestClient) -> None:
    guide_state = start_login(client, "/guides/sso-integration-guide/")
    security_state = start_login(client, "/security/")

    complete_login(client, guide_state, "code-guide")
    complete_login(client, security_state, "code-security")

    assert current_session(client)[SESSION_USER_KEY]["sub"] == "user-code-security"


# --- Unknown, missing, and replayed state -------------------------------------


@pytest.mark.parametrize("query", ["code=abc", "code=abc&state=", "code=abc&state=not-a-state"])
def test_a_callback_without_a_known_state_fails_closed(client: TestClient, query: str) -> None:
    response = client.get(f"/_auth/callback?{query}", follow_redirects=False)

    assert response.status_code == 400
    assert "user-" not in response.text
    assert SESSION_USER_KEY not in current_session(client)


def test_an_unknown_state_leaves_other_pending_logins_alone(client: TestClient) -> None:
    """Otherwise anyone could invalidate a victim's in-flight login."""
    guide_state = start_login(client, "/guides/sso-integration-guide/")

    client.get("/_auth/callback?code=abc&state=forged", follow_redirects=False)

    assert list(current_session(client)[SESSION_PENDING_LOGINS_KEY]) == [guide_state]
    assert complete_login(client, guide_state, "code-guide").headers["location"] == (
        "/guides/sso-integration-guide/"
    )


def test_a_state_cannot_be_replayed(client: TestClient) -> None:
    guide_state = start_login(client, "/guides/sso-integration-guide/")
    complete_login(client, guide_state, "code-guide")

    replay = complete_login(client, guide_state, "code-guide")

    assert replay.status_code == 400


def test_a_failed_callback_does_not_take_down_other_tabs(
    client: TestClient, token_endpoint: RecordingTokenEndpoint
) -> None:
    guide_state = start_login(client, "/guides/sso-integration-guide/")
    security_state = start_login(client, "/security/")

    async def issuer_omits_the_subject(**params):
        return {"access_token": "opaque", "userinfo": {"email": "user@example.com"}}

    client.app.state.auth_client.fetch_access_token = issuer_omits_the_subject
    failed = client.get(
        f"/_auth/callback?code=bad&state={guide_state}", follow_redirects=False
    )
    assert failed.status_code == 500
    assert SESSION_USER_KEY not in current_session(client)

    client.app.state.auth_client.fetch_access_token = token_endpoint
    recovered = complete_login(client, security_state, "code-security")
    assert recovered.headers["location"] == "/security/"


# --- Cookie growth ------------------------------------------------------------


def test_pending_logins_are_capped(client: TestClient) -> None:
    """Anyone can open `/_auth/login`; the signed cookie must stay bounded."""
    states = [start_login(client, f"/guides/guide-{index}/") for index in range(MAX_PENDING_LOGINS + 3)]

    session = current_session(client)
    pending = session[SESSION_PENDING_LOGINS_KEY]

    assert len(pending) == MAX_PENDING_LOGINS
    assert list(pending) == states[-MAX_PENDING_LOGINS:]


def test_capping_also_drops_the_authlib_entry_for_the_evicted_login(
    client: TestClient,
) -> None:
    """The PKCE verifier and nonce are the bulky half of each transaction."""
    states = [start_login(client, f"/guides/guide-{index}/") for index in range(MAX_PENDING_LOGINS + 1)]

    session = current_session(client)
    state_keys = [key for key in session if key.startswith("_state_authifi_")]

    assert oauth_state_session_key(states[0]) not in session
    assert len(state_keys) == MAX_PENDING_LOGINS


def test_the_evicted_login_fails_closed_rather_than_landing_anywhere(
    client: TestClient,
) -> None:
    states = [start_login(client, f"/guides/guide-{index}/") for index in range(MAX_PENDING_LOGINS + 1)]

    assert complete_login(client, states[0], "code-0").status_code == 400
    assert complete_login(client, states[-1], "code-last").headers["location"] == (
        f"/guides/guide-{MAX_PENDING_LOGINS}/"
    )


def test_the_session_cookie_stays_well_under_the_browser_limit(client: TestClient) -> None:
    for index in range(MAX_PENDING_LOGINS + 5):
        start_login(client, f"/guides/guide-{index}/")

    assert len(client.cookies[SESSION_COOKIE_NAME]) < 3000


# --- Session fixation ---------------------------------------------------------


def test_a_successful_login_discards_everything_but_pending_transactions(
    client: TestClient,
) -> None:
    """Anything planted in the cookie before login must not outlive it."""
    guide_state = start_login(client, "/guides/sso-integration-guide/")
    security_state = start_login(client, "/security/")

    complete_login(client, guide_state, "code-guide")

    session = current_session(client)
    assert set(session) == {
        SESSION_USER_KEY,
        SESSION_PENDING_LOGINS_KEY,
        oauth_state_session_key(security_state),
    }


def test_a_pre_existing_user_is_replaced_not_merged(client: TestClient) -> None:
    guide_state = start_login(client, "/guides/sso-integration-guide/")
    planted = current_session(client)
    planted[SESSION_USER_KEY] = {"sub": "attacker", "email": "attacker@example.com"}
    planted["souvenir"] = "should not survive"

    # A separate client, so the planted cookie is the only one in the jar.
    tampered = TestClient(client.app)
    tampered.cookies.set(SESSION_COOKIE_NAME, encode_session_cookie(planted))
    response = complete_login(tampered, guide_state, "code-guide")

    session = decode_session_cookie(extract_cookie_value(response.headers["set-cookie"]))
    assert session[SESSION_USER_KEY] == {
        "sub": "user-code-guide",
        "email": "code-guide@example.com",
    }
    assert "souvenir" not in session
