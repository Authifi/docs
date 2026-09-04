from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from server.local_smoke import (
    BYPASS_PROBE_PATHS,
    PUBLIC_MIME_PROBES,
    assert_content_type,
    assert_no_protected_content,
    build_compose_command,
    build_mock_compose_env,
    classify_logout_redirect,
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
            "MOCK_OIDC_HOST": "issuer.example.test",
            "MOCK_OIDC_PORT": "9444",
        },
    )
    for key in ("OIDC_ISSUER", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET", "SESSION_SECRET"):
        assert key not in env


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


def test_bypass_probes_cover_every_public_prefix() -> None:
    from server.app import PUBLIC_PREFIXES

    for prefix in PUBLIC_PREFIXES:
        assert any(path.startswith(prefix) for path in BYPASS_PROBE_PATHS), prefix
    assert any("search_index.json" in path for path in BYPASS_PROBE_PATHS)


def test_public_mime_probes_cover_every_exact_public_path() -> None:
    from server.app import PUBLIC_EXACT_PATHS

    probed = {path for path, _ in PUBLIC_MIME_PROBES}
    assert PUBLIC_EXACT_PATHS <= probed


def test_assert_no_protected_content_rejects_successful_bypass() -> None:
    with pytest.raises(AssertionError, match="expected 404"):
        assert_no_protected_content("/assets/%2e%2e/index.html", 200, "")


def test_assert_no_protected_content_rejects_leaked_markers() -> None:
    with pytest.raises(AssertionError, match="leaked protected content marker"):
        assert_no_protected_content("/assets/%2e%2e/index.html", 404, '{"docs": []}')


def test_assert_no_protected_content_accepts_clean_404() -> None:
    assert_no_protected_content("/assets/%2e%2e/index.html", 404, "Not Found")


def test_assert_content_type_rejects_octet_stream() -> None:
    with pytest.raises(AssertionError, match="text/html"):
        assert_content_type("/privacy-policy/", "application/octet-stream", "text/html; charset=utf-8")


@pytest.mark.parametrize(
    ("location", "expected_mode"),
    [
        ("/terms-of-service/", "local"),
        ("http://oidc-mock.127.0.0.1.nip.io:9400/session/end?client_id=x", "rp-initiated"),
    ],
)
def test_classify_logout_redirect_supports_both_modes(
    tmp_path: Path, location: str, expected_mode: str
) -> None:
    settings = resolve_settings(tmp_path, environ={})

    assert classify_logout_redirect(location, settings) == expected_mode


def test_classify_logout_redirect_rejects_foreign_targets(tmp_path: Path) -> None:
    settings = resolve_settings(tmp_path, environ={})

    with pytest.raises(AssertionError, match="unexpected logout redirect target"):
        classify_logout_redirect("https://evil.example/", settings)


def assert_required_values(values: Mapping[str, str], expected: Mapping[str, str]) -> None:
    for key, expected_value in expected.items():
        assert values[key] == expected_value
