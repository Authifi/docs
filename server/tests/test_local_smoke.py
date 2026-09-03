from __future__ import annotations

from pathlib import Path

import pytest

from server.local_smoke import build_compose_command, require_redirect


def test_build_compose_command_includes_all_compose_files(tmp_path: Path) -> None:
    command = build_compose_command(
        project_dir=tmp_path,
        compose_files=("compose.yaml", "compose.mock.yaml"),
        args=("up", "-d"),
    )

    assert command == [
        "docker",
        "compose",
        "--project-directory",
        str(tmp_path),
        "-f",
        str(tmp_path / "compose.yaml"),
        "-f",
        str(tmp_path / "compose.mock.yaml"),
        "up",
        "-d",
    ]


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ("/_auth/login?next=%2F", "%2F"),
        ("/_auth/login?next=%2Fguides%2Ftenant-admin-guide%2F", "%2Fguides%2Ftenant-admin-guide%2F"),
    ],
)
def test_require_redirect_accepts_login_redirects(location: str, expected: str) -> None:
    assert require_redirect(status_code=307, location=location, expected_prefix="/_auth/login?next=") == expected


def test_require_redirect_rejects_unexpected_status_code() -> None:
    with pytest.raises(AssertionError, match="expected status 307"):
        require_redirect(
            status_code=200,
            location="/_auth/login?next=%2F",
            expected_prefix="/_auth/login?next=",
        )


def test_require_redirect_accepts_custom_expected_status_code() -> None:
    assert (
        require_redirect(
            status_code=302,
            location="http://issuer.example.com/oauth2/authorize?state=abc",
            expected_prefix="http://issuer.example.com/oauth2/authorize",
            expected_status=302,
        )
        == "?state=abc"
    )


def test_require_redirect_rejects_unexpected_location() -> None:
    with pytest.raises(AssertionError, match="expected location starting with"):
        require_redirect(
            status_code=307,
            location="/wrong",
            expected_prefix="/_auth/login?next=",
        )
