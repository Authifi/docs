"""What the issuer is allowed to put in the session cookie.

Everything the callback keeps is stored in a signed cookie the browser will
silently drop past 4096 bytes, and it is the issuer, not this server, that
decides how long a `sub`, `email`, or `name` is. A tenant with a long-winded
directory, or an issuer under someone else's control, could otherwise push the
cookie past that limit and break every session it touched -- so each claim is
bounded in bytes, the required one fails closed, and the optional ones are
dropped rather than allowed to overflow.

Bytes rather than characters: a name in Japanese is three bytes a character, and
the cookie counts bytes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.app import (
    MAX_EMAIL_BYTES,
    MAX_NAME_BYTES,
    MAX_SUBJECT_BYTES,
    OPTIONAL_CLAIM_LIMITS,
    extract_minimal_user,
)
from server.tests.support import (
    DummyAuthClient,
    authenticated_client,
    decode_session_cookie,
    extract_cookie_value,
    replay_client,
)


def sized(text: str, size: int) -> str:
    """`text`, padded to exactly `size` bytes of UTF-8."""
    padded = text + "a" * (size - len(text.encode("utf-8")))
    assert len(padded.encode("utf-8")) == size
    return padded


def callback_session(site_dir: Path, userinfo: dict) -> dict:
    auth_client = DummyAuthClient(token={"userinfo": userinfo})
    client = authenticated_client(
        site_dir, auth_client=auth_client, session={"pending_logins": {"st": "/"}}
    )

    response = client.get("/_auth/callback?code=c&state=st", follow_redirects=False)

    assert response.status_code == 307, response.text
    return decode_session_cookie(extract_cookie_value(response.headers["set-cookie"]))


# --- The subject is required, and bounded -------------------------------------


def test_a_subject_at_the_limit_is_accepted() -> None:
    subject = sized("sub-", MAX_SUBJECT_BYTES)

    assert extract_minimal_user({"sub": subject}) == {"sub": subject}


def test_a_subject_one_byte_over_the_limit_is_refused() -> None:
    with pytest.raises(ValueError, match="subject"):
        extract_minimal_user({"sub": sized("sub-", MAX_SUBJECT_BYTES + 1)})


def test_the_subject_limit_counts_bytes_rather_than_characters() -> None:
    """`MAX_SUBJECT_BYTES` characters of Japanese is three times the budget."""
    too_long = "あ" * MAX_SUBJECT_BYTES

    assert len(too_long) == MAX_SUBJECT_BYTES
    with pytest.raises(ValueError, match="subject"):
        extract_minimal_user({"sub": too_long})


def test_a_multibyte_subject_is_accepted_right_up_to_the_limit() -> None:
    subject = "あ" * (MAX_SUBJECT_BYTES // 3)

    assert extract_minimal_user({"sub": subject}) == {"sub": subject}


@pytest.mark.parametrize(
    "subject",
    [None, "", " ", {"nested": "object"}, ["list"], True, "with\nnewline", "with\x00null"],
    ids=["missing", "empty", "whitespace", "object", "list", "boolean", "newline", "null"],
)
def test_a_malformed_subject_is_refused(subject: object) -> None:
    """None of these identifies anyone, and a container is not a claim.

    A newline is refused because the value reaches log lines, and a bare `True`
    because `isinstance(True, int)` would otherwise make it the subject `"True"`.
    """
    with pytest.raises(ValueError, match="subject"):
        extract_minimal_user({"sub": subject})


def test_a_numeric_subject_is_carried_as_its_digits() -> None:
    """The spec says string, and some issuers send a number anyway.

    Converting an integer is lossless and unambiguous, unlike stringifying a
    container, so this one conversion is allowed.
    """
    assert extract_minimal_user({"sub": 24400320}) == {"sub": "24400320"}


def test_no_userinfo_at_all_is_refused() -> None:
    with pytest.raises(ValueError, match="subject"):
        extract_minimal_user(None)


def test_the_refusal_never_repeats_the_claim_it_refused(site_dir: Path) -> None:
    """The value is issuer-controlled and unbounded; the message must not carry it."""
    secret = "x" * 9000

    with pytest.raises(ValueError) as refusal:
        extract_minimal_user({"sub": secret})

    assert secret not in str(refusal.value)
    assert len(str(refusal.value)) < 200


def test_an_overlong_subject_leaves_no_session_behind(site_dir: Path) -> None:
    auth_client = DummyAuthClient(
        token={"userinfo": {"sub": sized("s-", MAX_SUBJECT_BYTES + 1)}}
    )
    client = authenticated_client(
        site_dir,
        auth_client=auth_client,
        session={"pending_logins": {"st": "/"}, "user": {"sub": "stale"}},
    )

    callback = client.get("/_auth/callback?code=c&state=st", follow_redirects=False)
    assert callback.status_code == 500

    replayed = replay_client(site_dir, callback).get("/", follow_redirects=False)
    assert replayed.status_code == 307
    assert replayed.headers["location"] == "/_auth/login?next=%2F"


# --- The optional claims are dropped, never truncated -------------------------


def test_the_optional_claims_are_the_two_documented_ones() -> None:
    assert OPTIONAL_CLAIM_LIMITS == {"email": MAX_EMAIL_BYTES, "name": MAX_NAME_BYTES}


@pytest.mark.parametrize("field", sorted(OPTIONAL_CLAIM_LIMITS))
def test_an_optional_claim_at_its_limit_is_kept(field: str) -> None:
    value = sized(f"{field}-", OPTIONAL_CLAIM_LIMITS[field])

    assert extract_minimal_user({"sub": "s", field: value}) == {"sub": "s", field: value}


@pytest.mark.parametrize("field", sorted(OPTIONAL_CLAIM_LIMITS))
def test_an_oversized_optional_claim_is_dropped_and_the_login_succeeds(field: str) -> None:
    """Dropped, not truncated: half an email address is a wrong one.

    Access does not depend on either claim -- v1 authorises on the subject
    alone -- so losing a display value is strictly better than refusing a
    legitimate sign-in or breaking the cookie.
    """
    oversized = sized(f"{field}-", OPTIONAL_CLAIM_LIMITS[field] + 1)

    assert extract_minimal_user({"sub": "s", field: oversized}) == {"sub": "s"}


@pytest.mark.parametrize("field", sorted(OPTIONAL_CLAIM_LIMITS))
def test_an_oversized_optional_claim_is_not_stored_in_the_session(
    site_dir: Path, field: str
) -> None:
    oversized = sized(f"{field}-", OPTIONAL_CLAIM_LIMITS[field] + 1)

    session = callback_session(site_dir, {"sub": "user-123", field: oversized})

    assert session["user"] == {"sub": "user-123"}
    assert oversized[:64] not in str(session)


@pytest.mark.parametrize(
    "value",
    [{"nested": "object"}, ["list"], 42, "with\nnewline"],
    ids=["object", "list", "number", "newline"],
)
def test_a_malformed_optional_claim_is_dropped_rather_than_stringified(value: object) -> None:
    assert extract_minimal_user({"sub": "s", "email": value, "name": value}) == {"sub": "s"}


def test_the_optional_limits_count_bytes_too() -> None:
    multibyte = "あ" * MAX_NAME_BYTES

    assert extract_minimal_user({"sub": "s", "name": multibyte}) == {"sub": "s"}


def test_nothing_beyond_the_three_claims_is_ever_stored(site_dir: Path) -> None:
    """The issuer can send anything; only these three are ours to keep."""
    session = callback_session(
        site_dir,
        {
            "sub": "user-123",
            "email": "user@example.com",
            "name": "Example User",
            "groups": ["admin"] * 500,
            "picture": "https://issuer.example.com/" + "p" * 4000,
        },
    )

    assert set(session["user"]) == {"sub", "email", "name"}
    assert "groups" not in str(session)
    assert "picture" not in str(session)
