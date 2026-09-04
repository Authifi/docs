import importlib
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import ANY

import pytest
from starlette.testclient import TestClient

from server.app import (
    DEFAULT_POST_LOGOUT_PATH,
    SESSION_MAX_AGE_SECONDS,
    AppConfig,
    create_app,
    normalize_next_path,
)
from server.tests.support import (
    DummyAuthClient,
    NoDiscoveryAuthClient,
    authenticated_client,
    build_client,
    build_config,
    decode_session_cookie,
    encode_session_cookie,
    extract_cookie_value,
    replay_client,
)


def test_redirects_unauthenticated_root_to_login_with_safe_next(site_dir: Path) -> None:
    client = build_client(site_dir)

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/_auth/login?next=%2F"


@pytest.mark.parametrize(
    "path",
    [
        "/privacy-policy/",
        "/terms-of-service/",
        "/sms-opt-in.html",
        "/sitemap.xml",
        "/robots.txt",
        "/auth.md",
        "/.well-known/api-catalog",
        "/.well-known/agent-skills/index.json",
        "/assets/app.css",
        "/javascripts/app.js",
        "/stylesheets/app.css",
        "/health",
    ],
)
def test_allows_public_paths_without_session(site_dir: Path, path: str) -> None:
    client = build_client(site_dir)

    response = client.get(path, follow_redirects=False)

    assert response.status_code == 200


def test_redirects_private_search_index_without_session(site_dir: Path) -> None:
    client = build_client(site_dir)

    response = client.get("/search/search_index.json", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/_auth/login?next=%2Fsearch%2Fsearch_index.json"


@pytest.mark.parametrize(
    ("path", "expected_next"),
    [
        ("/assets", "%2Fassets"),
        ("/javascripts", "%2Fjavascripts"),
        ("/stylesheets", "%2Fstylesheets"),
    ],
)
def test_allowlist_boundaries_still_require_auth(site_dir: Path, path: str, expected_next: str) -> None:
    client = build_client(site_dir)

    response = client.get(path, follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == f"/_auth/login?next={expected_next}"


def test_serves_authenticated_root_with_link_headers(site_dir: Path) -> None:
    client = authenticated_client(site_dir)

    response = client.get("/")

    assert response.status_code == 200
    assert response.text == "<h1>Private home</h1>"
    assert response.headers.get_list("link") == [
        '</.well-known/api-catalog>; rel="api-catalog"',
        '</guides/nhe-delegated-tokens/>; rel="service-doc"',
        '</guides/sso-integration-guide/>; rel="service-doc"',
        '</security/recommended-secure-configuration/>; rel="service-doc"',
    ]


@pytest.mark.parametrize(
    ("path", "content_type"),
    [
        (
            "/.well-known/api-catalog",
            'application/linkset+json; profile="https://www.rfc-editor.org/info/rfc9727"',
        ),
        ("/.well-known/agent-skills/index.json", "application/json"),
        ("/auth.md", "text/markdown; charset=utf-8"),
        ("/privacy-policy/", "text/html; charset=utf-8"),
        ("/terms-of-service/", "text/html; charset=utf-8"),
        ("/sms-opt-in.html", "text/html; charset=utf-8"),
        ("/assets/app.css", "text/css; charset=utf-8"),
        ("/assets/app.js", "text/javascript; charset=utf-8"),
        ("/assets/logo.png", "image/png"),
        ("/javascripts/app.js", "text/javascript; charset=utf-8"),
        ("/stylesheets/app.css", "text/css; charset=utf-8"),
        ("/robots.txt", "text/plain; charset=utf-8"),
        ("/sitemap.xml", "application/xml"),
    ],
)
def test_applies_required_content_types(site_dir: Path, path: str, content_type: str) -> None:
    client = build_client(site_dir)

    response = client.get(path)

    assert response.status_code == 200
    assert response.headers["content-type"] == content_type


@pytest.mark.parametrize(
    ("path", "content_type"),
    [
        ("/", "text/html; charset=utf-8"),
        ("/guides/sso-integration-guide/", "text/html; charset=utf-8"),
        ("/security/", "text/html; charset=utf-8"),
        ("/search/search_index.json", "application/json"),
    ],
)
def test_protected_paths_get_correct_content_types_after_login(
    site_dir: Path, path: str, content_type: str
) -> None:
    client = authenticated_client(site_dir)

    response = client.get(path)

    assert response.status_code == 200
    assert response.headers["content-type"] == content_type


def test_serves_directory_pages_with_file_response_validators(site_dir: Path) -> None:
    client = build_client(site_dir)

    response = client.get("/privacy-policy/")

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert response.headers["accept-ranges"] == "bytes"
    assert "etag" in response.headers
    assert "last-modified" in response.headers


def test_blocks_traversal_attempt_after_authentication(site_dir: Path) -> None:
    client = authenticated_client(site_dir)

    response = client.get("/../secret.txt")

    assert response.status_code == 404


def test_returns_404_for_missing_private_file_after_authentication(site_dir: Path) -> None:
    client = authenticated_client(site_dir)

    response = client.get("/missing/page/")

    assert response.status_code == 404


# --- Directory canonicalisation (308) ----------------------------------------


@pytest.mark.parametrize("path", ["/privacy-policy", "/terms-of-service"])
def test_public_directory_routes_canonicalise_publicly(site_dir: Path, path: str) -> None:
    client = build_client(site_dir)

    response = client.get(path, follow_redirects=False)

    assert response.status_code == 308
    assert response.headers["location"] == f"{path}/"
    assert response.headers["cache-control"] == "public, max-age=300"


@pytest.mark.parametrize("path", ["/guides/sso-integration-guide", "/security"])
def test_protected_directory_routes_canonicalise_without_leaking_content(
    site_dir: Path, path: str
) -> None:
    client = build_client(site_dir)

    response = client.get(path, follow_redirects=False)

    assert response.status_code == 308
    assert response.headers["location"] == f"{path}/"
    assert response.headers["cache-control"] == "private, no-store"
    assert "Security overview" not in response.text
    assert "SSO integration guide" not in response.text


def test_directory_canonicalisation_preserves_query_string(site_dir: Path) -> None:
    client = build_client(site_dir)

    response = client.get("/privacy-policy?highlight=cookies", follow_redirects=False)

    assert response.status_code == 308
    assert response.headers["location"] == "/privacy-policy/?highlight=cookies"


@pytest.mark.parametrize("path", ["/missing-page", "/assets/app.css", "/assets", "/guides"])
def test_files_and_missing_paths_are_never_canonicalised(site_dir: Path, path: str) -> None:
    client = build_client(site_dir)

    response = client.get(path, follow_redirects=False)

    assert response.status_code != 308


# --- Security and cache headers ----------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["/privacy-policy/", "/robots.txt", "/health", "/", "/_auth/logout"],
)
def test_baseline_security_headers_present_on_all_responses(site_dir: Path, path: str) -> None:
    client = build_client(site_dir)

    response = client.get(path, follow_redirects=False)

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def test_protected_responses_are_private_and_vary_on_cookie(site_dir: Path) -> None:
    client = authenticated_client(site_dir)

    response = client.get("/")

    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["vary"] == "Cookie"


def test_public_responses_are_cacheable(site_dir: Path) -> None:
    client = build_client(site_dir)

    response = client.get("/privacy-policy/")

    assert response.headers["cache-control"] == "public, max-age=300"


@pytest.mark.parametrize("path", ["/", "/search/search_index.json", "/guides/sso-integration-guide/"])
def test_login_redirects_are_not_cached(site_dir: Path, path: str) -> None:
    client = build_client(site_dir)

    response = client.get(path, follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["vary"] == "Cookie"


@pytest.mark.parametrize("path", ["/_auth/login", "/_auth/logout", "/_auth/callback"])
def test_auth_responses_are_not_cached(site_dir: Path, path: str) -> None:
    client = authenticated_client(site_dir, session={"next": "/"})

    response = client.get(path, follow_redirects=False)

    assert response.headers["cache-control"] == "private, no-store"


def test_hsts_is_sent_only_for_https_base_url(site_dir: Path) -> None:
    secure_client = build_client(site_dir, public_base_url="https://docs.example.com")
    local_client = build_client(site_dir, public_base_url="http://localhost:8000")

    secure_response = secure_client.get("/privacy-policy/")
    local_response = local_client.get("/privacy-policy/")

    assert secure_response.headers["strict-transport-security"] == "max-age=63072000; includeSubDomains"
    assert "strict-transport-security" not in local_response.headers


# --- Session lifetime ---------------------------------------------------------


def test_session_cookie_uses_eight_hour_max_age(site_dir: Path) -> None:
    assert SESSION_MAX_AGE_SECONDS == 8 * 60 * 60
    client = build_client(site_dir)

    response = client.get("/_auth/login?next=/", follow_redirects=False)

    assert "max-age=28800" in response.headers["set-cookie"].lower()


def test_expired_session_cookie_is_rejected(site_dir: Path) -> None:
    client = build_client(site_dir)
    client.cookies.set(
        "authifi-session",
        encode_session_cookie({"user": {"sub": "user-123"}}, age_seconds=SESSION_MAX_AGE_SECONDS + 60),
    )

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/_auth/login?next=%2F"


def test_session_cookie_just_inside_max_age_is_accepted(site_dir: Path) -> None:
    client = build_client(site_dir)
    client.cookies.set(
        "authifi-session",
        encode_session_cookie({"user": {"sub": "user-123"}}, age_seconds=SESSION_MAX_AGE_SECONDS - 120),
    )

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 200


# --- Login / callback ---------------------------------------------------------


def test_login_redirects_and_persists_safe_next(site_dir: Path) -> None:
    auth_client = DummyAuthClient()
    client = build_client(site_dir, auth_client=auth_client)

    response = client.get("/_auth/login?next=/guides/sso-integration-guide/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "https://issuer.example.com/authorize"
    assert auth_client.redirect_calls == [{"redirect_uri": "https://docs.example.com/_auth/callback"}]
    session = decode_session_cookie(client.cookies["authifi-session"])
    assert session["next"] == "/guides/sso-integration-guide/"


def test_login_with_real_authlib_client_generates_pkce_challenge_and_persists_verifier(site_dir: Path) -> None:
    app = create_app(build_config(site_dir))

    async def fake_metadata() -> dict[str, str]:
        return {
            "authorization_endpoint": "https://issuer.example.com/oauth2/authorize",
            "token_endpoint": "https://issuer.example.com/oauth2/token",
            "jwks_uri": "https://issuer.example.com/.well-known/jwks.json",
        }

    app.state.auth_client.load_server_metadata = fake_metadata
    client = TestClient(app)

    response = client.get("/_auth/login?next=/guides/sso-integration-guide/", follow_redirects=False)

    assert response.status_code == 302
    params = parse_qs(urlparse(response.headers["location"]).query)
    assert params["code_challenge_method"] == ["S256"]
    assert params["code_challenge"] == [ANY]
    session = decode_session_cookie(client.cookies["authifi-session"])
    state_key = f"_state_authifi_{params['state'][0]}"
    assert session["next"] == "/guides/sso-integration-guide/"
    assert session[state_key]["data"]["code_verifier"] == ANY
    assert session[state_key]["data"]["nonce"] == params["nonce"][0]


@pytest.mark.parametrize(
    "unsafe_next",
    [
        "//evil.example",
        "https://evil.example/steal",
        "guides/no-leading-slash",
        "/\\evil.example",
        "/guides\\..\\secret",
        "/guides/\rSet-Cookie: x=1",
        "/guides/\nX-Injected: 1",
        "/guides/\x00",
        "/guides/\tvalue",
    ],
)
def test_login_rejects_unsafe_next_values(site_dir: Path, unsafe_next: str) -> None:
    auth_client = DummyAuthClient()
    client = build_client(site_dir, auth_client=auth_client)

    response = client.get("/_auth/login", params={"next": unsafe_next}, follow_redirects=False)

    assert response.status_code == 307
    session = decode_session_cookie(client.cookies["authifi-session"])
    assert session["next"] == "/"


@pytest.mark.parametrize(
    "unsafe_next",
    [
        "//evil.example",
        "https://evil.example/steal",
        "/\\evil.example",
        "/guides\\secret",
        "/guides\r\nX-Injected: 1",
        "/guides\x00",
        "",
        None,
    ],
)
def test_normalize_next_path_rejects_unsafe_values(unsafe_next: str | None) -> None:
    assert normalize_next_path(unsafe_next) == "/"


@pytest.mark.parametrize(
    "safe_next",
    ["/", "/guides/sso-integration-guide/", "/privacy-policy/", "/search/search_index.json"],
)
def test_normalize_next_path_accepts_safe_values(safe_next: str) -> None:
    assert normalize_next_path(safe_next) == safe_next


def test_normalize_next_path_honours_custom_default() -> None:
    assert normalize_next_path("//evil.example", default="/privacy-policy/") == "/privacy-policy/"


def test_callback_stores_minimal_identity_and_redirects_to_next(site_dir: Path) -> None:
    auth_client = DummyAuthClient()
    client = authenticated_client(site_dir, auth_client=auth_client, session={"next": "/security/"})

    response = client.get("/_auth/callback?code=abc&state=def", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/security/"
    session = decode_session_cookie(extract_cookie_value(response.headers["set-cookie"]))
    assert session == {
        "user": {
            "sub": "user-123",
            "email": "user@example.com",
            "name": "Example User",
        }
    }
    assert auth_client.token_requests == 1


def test_callback_does_not_persist_raw_tokens(site_dir: Path) -> None:
    auth_client = DummyAuthClient()
    client = authenticated_client(site_dir, auth_client=auth_client, session={"next": "/"})

    response = client.get("/_auth/callback?code=abc&state=def", follow_redirects=False)

    cookie_value = extract_cookie_value(response.headers["set-cookie"])
    session = decode_session_cookie(cookie_value)
    assert "opaque-id-token" not in str(session)
    assert "opaque-access-token" not in str(session)
    assert set(session) == {"user"}


def test_callback_rejects_unsafe_stored_next(site_dir: Path) -> None:
    auth_client = DummyAuthClient()
    client = authenticated_client(site_dir, auth_client=auth_client, session={"next": "//evil.example"})

    response = client.get("/_auth/callback?code=abc&state=def", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/"


def test_callback_fails_closed_when_subject_claim_is_missing(
    site_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    auth_client = DummyAuthClient(
        token={
            "userinfo": {"email": "user@example.com"},
            "id_token": "super-secret-id-token",
            "access_token": "super-secret-access-token",
        }
    )
    client = authenticated_client(site_dir, auth_client=auth_client, session={"next": "/"})

    with caplog.at_level("ERROR"):
        response = client.get("/_auth/callback?code=abc&state=def", follow_redirects=False)

    assert response.status_code == 500
    assert "subject" in response.text.lower() or "authentication" in response.text.lower()
    assert "super-secret-id-token" not in response.text
    assert "super-secret-access-token" not in response.text
    log_text = caplog.text
    assert "subject" in log_text.lower()
    assert "super-secret-id-token" not in log_text
    assert "super-secret-access-token" not in log_text


def test_callback_clears_session_when_subject_claim_is_missing(site_dir: Path) -> None:
    auth_client = DummyAuthClient(token={"userinfo": {"email": "user@example.com"}})
    client = authenticated_client(
        site_dir, auth_client=auth_client, session={"next": "/", "user": {"sub": "stale"}}
    )

    callback_response = client.get("/_auth/callback?code=abc&state=def", follow_redirects=False)

    response = replay_client(site_dir, callback_response).get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/_auth/login?next=%2F"


# --- Logout -------------------------------------------------------------------


def test_logout_uses_rp_initiated_end_session_endpoint_when_discovered(site_dir: Path) -> None:
    auth_client = DummyAuthClient(
        metadata={"end_session_endpoint": "https://issuer.example.com/oidc/logout"}
    )
    client = authenticated_client(site_dir, auth_client=auth_client)

    response = client.get("/_auth/logout?next=/terms-of-service/", follow_redirects=False)

    assert response.status_code == 307
    location = urlparse(response.headers["location"])
    assert location.scheme == "https"
    assert location.netloc == "issuer.example.com"
    assert location.path == "/oidc/logout"
    params = parse_qs(location.query)
    assert params["post_logout_redirect_uri"] == ["https://docs.example.com/terms-of-service/"]
    assert params["client_id"] == ["client-id"]
    assert "expires=Thu, 01 Jan 1970 00:00:00 GMT" in response.headers["set-cookie"]


def test_logout_preserves_existing_end_session_query_parameters(site_dir: Path) -> None:
    auth_client = DummyAuthClient(
        metadata={"end_session_endpoint": "https://issuer.example.com/oidc/logout?tenant=acme"}
    )
    client = authenticated_client(site_dir, auth_client=auth_client)

    response = client.get("/_auth/logout", follow_redirects=False)

    params = parse_qs(urlparse(response.headers["location"]).query)
    assert params["tenant"] == ["acme"]
    assert params["client_id"] == ["client-id"]
    assert params["post_logout_redirect_uri"] == [
        f"https://docs.example.com{DEFAULT_POST_LOGOUT_PATH}"
    ]


def test_logout_falls_back_to_configured_public_path_without_end_session_endpoint(
    site_dir: Path,
) -> None:
    auth_client = DummyAuthClient(metadata={})
    client = authenticated_client(site_dir, auth_client=auth_client)

    response = client.get("/_auth/logout", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == DEFAULT_POST_LOGOUT_PATH
    assert "expires=Thu, 01 Jan 1970 00:00:00 GMT" in response.headers["set-cookie"]


def test_logout_falls_back_when_discovery_is_unavailable(site_dir: Path) -> None:
    auth_client = DummyAuthClient(metadata_error=RuntimeError("issuer unreachable"))
    client = authenticated_client(site_dir, auth_client=auth_client)

    response = client.get("/_auth/logout?next=/privacy-policy/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/privacy-policy/"


def test_logout_falls_back_when_client_has_no_discovery_support(site_dir: Path) -> None:
    client = authenticated_client(site_dir, auth_client=NoDiscoveryAuthClient())

    response = client.get("/_auth/logout?next=/terms-of-service/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/terms-of-service/"


def test_logout_clears_local_session_even_when_redirecting_to_issuer(site_dir: Path) -> None:
    auth_client = DummyAuthClient(
        metadata={"end_session_endpoint": "https://issuer.example.com/oidc/logout"}
    )
    client = authenticated_client(site_dir, auth_client=auth_client)

    logout_response = client.get("/_auth/logout", follow_redirects=False)

    response = replay_client(site_dir, logout_response, auth_client=auth_client).get(
        "/", follow_redirects=False
    )
    assert response.status_code == 307
    assert response.headers["location"] == "/_auth/login?next=%2F"


def test_logout_rejects_unsafe_next_and_uses_configured_default(site_dir: Path) -> None:
    client = authenticated_client(site_dir, auth_client=DummyAuthClient())

    response = client.get("/_auth/logout", params={"next": "//evil.example"}, follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == DEFAULT_POST_LOGOUT_PATH


def test_logout_uses_custom_configured_post_logout_path(site_dir: Path) -> None:
    client = authenticated_client(site_dir, post_logout_path="/terms-of-service/")

    response = client.get("/_auth/logout", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/terms-of-service/"


# --- Misc ---------------------------------------------------------------------


def test_unauthenticated_internal_auth_file_still_requires_auth(site_dir: Path) -> None:
    protected_auth_file = site_dir / "_auth" / "secret.html"
    protected_auth_file.parent.mkdir(parents=True, exist_ok=True)
    protected_auth_file.write_text("<h1>Protected</h1>", encoding="utf-8")
    client = build_client(site_dir)

    response = client.get("/_auth/secret.html", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/_auth/login?next=%2F_auth%2Fsecret.html"


def test_session_cookie_is_secure_for_https_base_url(site_dir: Path) -> None:
    client = build_client(site_dir, auth_client=DummyAuthClient())

    response = client.get("/_auth/login?next=/", follow_redirects=False)

    assert response.status_code == 307
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "samesite=lax" in response.headers["set-cookie"].lower()
    assert "secure" in response.headers["set-cookie"].lower()


def test_session_cookie_omits_secure_for_local_http(site_dir: Path) -> None:
    client = build_client(site_dir, public_base_url="http://localhost:8000", auth_client=DummyAuthClient())

    response = client.get("/_auth/login?next=/", follow_redirects=False)

    assert response.status_code == 307
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "samesite=lax" in response.headers["set-cookie"].lower()
    assert "secure" not in response.headers["set-cookie"].lower()


def test_health_returns_expected_payload(site_dir: Path) -> None:
    client = build_client(site_dir)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_app_config_reads_environment_defaults() -> None:
    config = AppConfig.from_env(
        {
            "OIDC_ISSUER": "https://issuer.example.com",
            "OIDC_CLIENT_ID": "client-id",
            "OIDC_CLIENT_SECRET": "client-secret",
            "SESSION_SECRET": "session-secret",
            "PUBLIC_BASE_URL": "https://docs.example.com",
        }
    )

    assert config.site_dir.name == "site"
    assert config.cookie_secure is True
    assert config.post_logout_path == DEFAULT_POST_LOGOUT_PATH
    assert config.session_max_age_seconds == SESSION_MAX_AGE_SECONDS


def test_app_config_reads_post_logout_path_from_environment() -> None:
    config = AppConfig.from_env(
        {
            "OIDC_ISSUER": "https://issuer.example.com",
            "OIDC_CLIENT_ID": "client-id",
            "OIDC_CLIENT_SECRET": "client-secret",
            "SESSION_SECRET": "session-secret",
            "PUBLIC_BASE_URL": "https://docs.example.com",
            "POST_LOGOUT_PATH": "/terms-of-service/",
        }
    )

    assert config.post_logout_path == "/terms-of-service/"


def test_app_config_rejects_unsafe_post_logout_path() -> None:
    config = AppConfig.from_env(
        {
            "OIDC_ISSUER": "https://issuer.example.com",
            "OIDC_CLIENT_ID": "client-id",
            "OIDC_CLIENT_SECRET": "client-secret",
            "SESSION_SECRET": "session-secret",
            "PUBLIC_BASE_URL": "https://docs.example.com",
            "POST_LOGOUT_PATH": "https://evil.example/",
        }
    )

    assert config.post_logout_path == DEFAULT_POST_LOGOUT_PATH


def test_main_module_exposes_asgi_app_from_environment(site_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OIDC_ISSUER", "https://issuer.example.com")
    monkeypatch.setenv("OIDC_CLIENT_ID", "client-id")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("SESSION_SECRET", "session-secret")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:8000")
    monkeypatch.setenv("SITE_DIR", str(site_dir))
    sys.modules.pop("server.main", None)

    module = importlib.import_module("server.main")

    assert getattr(module, "app", None) is not None
