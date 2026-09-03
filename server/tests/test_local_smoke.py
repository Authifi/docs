from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from server.local_smoke import (
    build_compose_command,
    build_mock_compose_env,
    parse_args,
    require_redirect,
    resolve_settings,
)


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


def test_resolve_settings_reads_custom_values_from_dotenv(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "DOCS_PORT=8123",
                "MOCK_OIDC_HOST=mock-issuer.example.test",
                "MOCK_OIDC_PORT=9555",
                "MOCK_OIDC_SUBJECT=casey@example.com",
            ]
        ),
        encoding="utf-8",
    )

    settings = resolve_settings(tmp_path, environ={})

    assert settings.public_base_url == "http://localhost:8123"
    assert settings.mock_issuer == "http://mock-issuer.example.test:9555"
    assert settings.subject == "casey@example.com"
    assert settings.discovery_url == "http://mock-issuer.example.test:9555/.well-known/openid-configuration"
    assert settings.user_url == "http://mock-issuer.example.test:9555/users/casey%40example.com"
    assert settings.login_url == "http://localhost:8123/_auth/login?next=%2F"


def test_resolve_settings_prefers_environment_over_dotenv(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("PUBLIC_BASE_URL=http://localhost:8000\n", encoding="utf-8")

    settings = resolve_settings(
        tmp_path,
        environ={"PUBLIC_BASE_URL": "http://127.0.0.1:9001", "MOCK_OIDC_SUBJECT": "env@example.com"},
    )

    assert settings.public_base_url == "http://127.0.0.1:9001"
    assert settings.subject == "env@example.com"


def test_build_mock_compose_env_supplies_required_mock_safe_values(tmp_path: Path) -> None:
    env = build_mock_compose_env(
        tmp_path,
        environ={"DOCS_PORT": "8999", "MOCK_OIDC_HOST": "issuer.example.test", "MOCK_OIDC_PORT": "9444"},
    )

    assert_required_values(
        env,
        {
            "PUBLIC_BASE_URL": "http://localhost:8999",
            "OIDC_ISSUER": "http://issuer.example.test:9444",
            "OIDC_CLIENT_ID": "local-docs-client",
            "OIDC_CLIENT_SECRET": "local-docs-secret",
            "SESSION_SECRET": "local-session-secret",
            "MOCK_OIDC_HOST": "issuer.example.test",
            "MOCK_OIDC_PORT": "9444",
        },
    )


def test_parse_args_uses_env_driven_defaults(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "DOCS_PORT=8456",
                "MOCK_OIDC_HOST=oidc.alt.test",
                "MOCK_OIDC_PORT=9666",
                "MOCK_OIDC_SUBJECT=pat@example.com",
            ]
        ),
        encoding="utf-8",
    )

    args = parse_args(["--project-dir", str(tmp_path)], environ={})

    assert args.public_base_url == "http://localhost:8456"
    assert args.mock_issuer == "http://oidc.alt.test:9666"
    assert args.subject == "pat@example.com"


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


def assert_required_values(values: Mapping[str, str], expected: Mapping[str, str]) -> None:
    for key, expected_value in expected.items():
        assert values[key] == expected_value
