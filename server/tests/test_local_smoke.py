from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest

from server import local_smoke
from server.local_smoke import (
    BYPASS_PROBE_PATHS,
    DEFAULT_POST_LOGOUT_PATH,
    EXISTENCE_PROBE_PATHS,
    PUBLIC_MIME_PROBES,
    assert_content_type,
    assert_no_existence_disclosure,
    assert_no_protected_content,
    build_compose_command,
    assert_registered_post_logout_uri,
    build_mock_compose_env,
    classify_logout_redirect,
    compose_diagnostics,
    dump_diagnostics,
    parse_args,
    require_redirect,
    resolve_settings,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


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


def test_build_mock_compose_env_pins_the_post_logout_path(tmp_path: Path) -> None:
    env = build_mock_compose_env(tmp_path, environ={})

    assert env["POST_LOGOUT_PATH"] == DEFAULT_POST_LOGOUT_PATH


def test_build_mock_compose_env_lets_the_environment_override_the_post_logout_path(
    tmp_path: Path,
) -> None:
    env = build_mock_compose_env(tmp_path, environ={"POST_LOGOUT_PATH": "/terms-of-service/"})

    assert env["POST_LOGOUT_PATH"] == "/terms-of-service/"


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
        ("/privacy-policy/", "local"),
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


def test_classify_logout_redirect_rejects_a_local_target_that_is_not_post_logout_path(
    tmp_path: Path,
) -> None:
    """`next` reaching the redirect is exactly the regression this guards."""
    settings = resolve_settings(tmp_path, environ={})

    with pytest.raises(AssertionError, match="unexpected logout redirect target"):
        classify_logout_redirect(settings.public_path, settings)


# --- Registered post-logout URI ----------------------------------------------


def test_logout_url_sends_a_next_that_must_be_ignored(tmp_path: Path) -> None:
    settings = resolve_settings(tmp_path, environ={})

    assert settings.public_path != settings.post_logout_path
    assert f"next={quote(settings.public_path, safe='')}" in settings.logout_url


def test_assert_registered_post_logout_uri_accepts_the_configured_target(tmp_path: Path) -> None:
    settings = resolve_settings(tmp_path, environ={})
    location = (
        f"{settings.mock_issuer}/session/end"
        f"?client_id=authifi-docs&post_logout_redirect_uri={quote(settings.post_logout_url, safe='')}"
    )

    assert_registered_post_logout_uri(location, settings)


def test_assert_registered_post_logout_uri_rejects_a_next_derived_target(tmp_path: Path) -> None:
    settings = resolve_settings(tmp_path, environ={})
    leaked = f"{settings.public_base_url.rstrip('/')}{settings.public_path}"
    location = (
        f"{settings.mock_issuer}/session/end"
        f"?client_id=authifi-docs&post_logout_redirect_uri={quote(leaked, safe='')}"
    )

    with pytest.raises(AssertionError, match="post_logout_redirect_uri"):
        assert_registered_post_logout_uri(location, settings)


def test_assert_registered_post_logout_uri_requires_client_id(tmp_path: Path) -> None:
    settings = resolve_settings(tmp_path, environ={})
    location = (
        f"{settings.mock_issuer}/session/end"
        f"?post_logout_redirect_uri={quote(settings.post_logout_url, safe='')}"
    )

    with pytest.raises(AssertionError, match="client_id"):
        assert_registered_post_logout_uri(location, settings)


def test_settings_track_the_configured_post_logout_path(tmp_path: Path) -> None:
    settings = resolve_settings(tmp_path, environ={"POST_LOGOUT_PATH": "/terms-of-service/"})

    assert settings.post_logout_path == "/terms-of-service/"
    assert settings.post_logout_url.endswith("/terms-of-service/")


def assert_required_values(values: Mapping[str, str], expected: Mapping[str, str]) -> None:
    for key, expected_value in expected.items():
        assert values[key] == expected_value


# --- Existence disclosure probes ---------------------------------------------


class StubResponse:
    def __init__(self, status_code: int, location: str | None = None, text: str = "") -> None:
        self.status_code = status_code
        self.headers = {"location": location} if location is not None else {}
        self.text = text


def login_redirect(path: str) -> StubResponse:
    return StubResponse(307, f"/_auth/login?next={quote(path, safe='')}")


def test_existence_probes_pair_a_real_route_with_a_missing_one() -> None:
    existing, missing = EXISTENCE_PROBE_PATHS

    assert (REPO_ROOT / "docs" / f"{existing.lstrip('/')}.md").is_file()
    assert not (REPO_ROOT / "docs" / f"{missing.lstrip('/')}.md").exists()
    assert existing.rsplit("/", 1)[0] == missing.rsplit("/", 1)[0]


def test_assert_no_existence_disclosure_accepts_matching_login_redirects() -> None:
    assert_no_existence_disclosure({path: login_redirect(path) for path in EXISTENCE_PROBE_PATHS})


def test_assert_no_existence_disclosure_rejects_a_canonicalising_redirect() -> None:
    existing, missing = EXISTENCE_PROBE_PATHS
    probes = {existing: StubResponse(308, f"{existing}/"), missing: login_redirect(missing)}

    with pytest.raises(AssertionError, match="login redirect"):
        assert_no_existence_disclosure(probes)


def test_assert_no_existence_disclosure_rejects_a_leaked_canonical_slash() -> None:
    existing, missing = EXISTENCE_PROBE_PATHS
    probes = {
        existing: StubResponse(307, f"/_auth/login?next={quote(existing + '/', safe='')}"),
        missing: login_redirect(missing),
    }

    with pytest.raises(AssertionError, match="did not echo the request"):
        assert_no_existence_disclosure(probes)


def test_assert_no_existence_disclosure_rejects_differing_bodies() -> None:
    existing, missing = EXISTENCE_PROBE_PATHS
    probes = {
        existing: StubResponse(307, f"/_auth/login?next={quote(existing, safe='')}", text="found"),
        missing: login_redirect(missing),
    }

    with pytest.raises(AssertionError, match="anonymous replies differ"):
        assert_no_existence_disclosure(probes)


# --- Failure diagnostics ------------------------------------------------------
#
# A CI auth 500 with no container logs cost a full debugging cycle, so a failed
# run has to leave the evidence behind before compose tears the stack down.


class RecordingRunner:
    def __init__(self, stdout: str = "log line") -> None:
        self.commands: list[list[str]] = []
        self.stdout = stdout

    def __call__(self, command, **kwargs):
        self.commands.append(list(command))
        return SimpleNamespace(returncode=0, stdout=self.stdout, stderr="")


def test_diagnostic_commands_cover_status_and_both_service_logs(tmp_path: Path) -> None:
    commands = compose_diagnostics(tmp_path)

    joined = [" ".join(command) for command in commands]
    assert any(command.endswith(" ps") for command in joined)
    for service in ("docs", "mock-oidc"):
        assert any(f"logs --no-color --tail" in c and c.endswith(service) for c in joined)


def test_diagnostic_commands_use_both_compose_files(tmp_path: Path) -> None:
    for command in compose_diagnostics(tmp_path):
        assert str(tmp_path / "compose.yaml") in command
        assert str(tmp_path / "compose.mock.yaml") in command


def test_dump_diagnostics_runs_every_command_and_reports_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = RecordingRunner(stdout="docs-1 | ValueError: boom")

    dump_diagnostics(tmp_path, {"PATH": "/usr/bin"}, runner=runner)

    assert runner.commands == compose_diagnostics(tmp_path)
    assert "docs-1 | ValueError: boom" in capsys.readouterr().err


def test_dump_diagnostics_survives_a_failing_docker_command(tmp_path: Path) -> None:
    def failing(command, **kwargs):
        raise OSError("docker is gone")

    # Diagnostics must never replace the original failure with their own.
    dump_diagnostics(tmp_path, {}, runner=failing)


def test_a_failed_smoke_dumps_diagnostics_before_tearing_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_run_compose(project_dir, env, *args):
        calls.append(f"compose {args[0]}")

    def fake_run_smoke(settings):
        calls.append("smoke")
        raise AssertionError("smoke failed")

    real_parse_args = local_smoke.parse_args
    monkeypatch.setattr(local_smoke, "run_compose", fake_run_compose)
    monkeypatch.setattr(local_smoke, "run_smoke", fake_run_smoke)
    monkeypatch.setattr(local_smoke, "dump_diagnostics", lambda *a, **k: calls.append("diagnostics"))
    monkeypatch.setattr(local_smoke, "parse_args", lambda: real_parse_args([]))

    with pytest.raises(AssertionError, match="smoke failed"):
        local_smoke.main()

    assert calls == ["compose up", "smoke", "diagnostics", "compose down"]


def test_a_passing_smoke_dumps_no_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    real_parse_args = local_smoke.parse_args
    monkeypatch.setattr(local_smoke, "run_compose", lambda p, e, *a: calls.append(f"compose {a[0]}"))
    monkeypatch.setattr(local_smoke, "run_smoke", lambda s: calls.append("smoke"))
    monkeypatch.setattr(local_smoke, "dump_diagnostics", lambda *a, **k: calls.append("diagnostics"))
    monkeypatch.setattr(local_smoke, "parse_args", lambda: real_parse_args([]))

    assert local_smoke.main() == 0
    assert calls == ["compose up", "smoke", "compose down"]
