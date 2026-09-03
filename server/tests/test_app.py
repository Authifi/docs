import importlib
import base64
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import ANY

import pytest
from itsdangerous import TimestampSigner
from starlette.responses import RedirectResponse
from starlette.testclient import TestClient

from server.app import AppConfig, create_app


class DummyAuthClient:
    def __init__(self, token: dict | None = None) -> None:
        self.token = token or {
            "userinfo": {
                "sub": "user-123",
                "email": "user@example.com",
                "name": "Example User",
                "role": "admin",
            },
            "id_token": "opaque-id-token",
            "access_token": "opaque-access-token",
        }
        self.redirect_calls: list[dict] = []
        self.token_requests = 0

    async def authorize_redirect(self, request, redirect_uri, **kwargs):
        self.redirect_calls.append({"redirect_uri": redirect_uri, **kwargs})
        return RedirectResponse("https://issuer.example.com/authorize")

    async def authorize_access_token(self, request):
        self.token_requests += 1
        return self.token


@pytest.fixture
def site_dir(tmp_path: Path) -> Path:
    files = {
        "index.html": "<h1>Private home</h1>",
        "privacy-policy/index.html": "<h1>Privacy</h1>",
        "terms-of-service/index.html": "<h1>Terms</h1>",
        "sms-opt-in.html": "<h1>SMS</h1>",
        "sitemap.xml": "<urlset></urlset>",
        "robots.txt": "User-agent: *\nAllow: /\n",
        "auth.md": "# Auth\n",
        ".well-known/api-catalog": '{"links":[]}',
        ".well-known/agent-skills/index.json": '{"skills":[]}',
        "assets/app.css": "body{}",
        "javascripts/app.js": "console.log('ok');",
        "stylesheets/app.css": "body{}",
        "search/search_index.json": '{"docs":[]}',
        "_headers": (
            "/\n"
            '  Link: </.well-known/api-catalog>; rel="api-catalog"\n'
            '  Link: </guides/nhe-delegated-tokens/>; rel="service-doc"\n'
            '  Link: </guides/sso-integration-guide/>; rel="service-doc"\n'
            '  Link: </security/recommended-secure-configuration/>; rel="service-doc"\n'
        ),
    }
    for relative_path, content in files.items():
        target = tmp_path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return tmp_path


def build_client(
    site_dir: Path,
    public_base_url: str = "https://docs.example.com",
    auth_client: DummyAuthClient | None = None,
) -> TestClient:
    config = build_config(site_dir, public_base_url=public_base_url)
    app = create_app(config=config, auth_client=auth_client or DummyAuthClient())
    return TestClient(app)


def build_config(site_dir: Path, public_base_url: str = "https://docs.example.com") -> AppConfig:
    return AppConfig(
        oidc_issuer="https://issuer.example.com",
        oidc_client_id="client-id",
        oidc_client_secret="client-secret",
        session_secret="session-secret",
        public_base_url=public_base_url,
        site_dir=site_dir,
    )


def authenticated_client(
    site_dir: Path,
    public_base_url: str = "https://docs.example.com",
    auth_client: DummyAuthClient | None = None,
    session: dict | None = None,
) -> TestClient:
    client = build_client(site_dir, public_base_url=public_base_url, auth_client=auth_client)
    client.cookies.set("authifi-session", encode_session_cookie(session or {"user": {"sub": "user-123"}}))
    return client


def encode_session_cookie(session: dict) -> str:
    signer = TimestampSigner("session-secret")
    session_data = base64.b64encode(json.dumps(session).encode("utf-8"))
    return signer.sign(session_data).decode("utf-8")


def decode_session_cookie(cookie_value: str) -> dict:
    signer = TimestampSigner("session-secret")
    signed_data = signer.unsign(cookie_value.encode("utf-8"))
    session_data = base64.b64decode(signed_data)
    return json.loads(session_data)


def extract_cookie_value(set_cookie_header: str) -> str:
    return set_cookie_header.split("authifi-session=", 1)[1].split(";", 1)[0]


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
        ("/privacy-policy", "%2Fprivacy-policy"),
        ("/terms-of-service", "%2Fterms-of-service"),
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
    ],
)
def test_applies_required_content_types(site_dir: Path, path: str, content_type: str) -> None:
    client = build_client(site_dir)

    response = client.get(path)

    assert response.status_code == 200
    assert response.headers["content-type"] == content_type


def test_blocks_traversal_attempt_after_authentication(site_dir: Path) -> None:
    client = authenticated_client(site_dir)

    response = client.get("/../secret.txt")

    assert response.status_code == 404


def test_returns_404_for_missing_private_file_after_authentication(site_dir: Path) -> None:
    client = authenticated_client(site_dir)

    response = client.get("/missing/page/")

    assert response.status_code == 404


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


@pytest.mark.parametrize("unsafe_next", ["//evil.example", "https://evil.example/steal", "guides/no-leading-slash"])
def test_login_rejects_unsafe_next_values(site_dir: Path, unsafe_next: str) -> None:
    auth_client = DummyAuthClient()
    client = build_client(site_dir, auth_client=auth_client)

    response = client.get(f"/_auth/login?next={unsafe_next}", follow_redirects=False)

    assert response.status_code == 307
    session = decode_session_cookie(client.cookies["authifi-session"])
    assert session["next"] == "/"


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


def test_callback_rejects_unsafe_stored_next(site_dir: Path) -> None:
    auth_client = DummyAuthClient()
    client = authenticated_client(site_dir, auth_client=auth_client, session={"next": "//evil.example"})

    response = client.get("/_auth/callback?code=abc&state=def", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/"


def test_logout_clears_session_and_redirects_to_safe_path(site_dir: Path) -> None:
    client = authenticated_client(site_dir, session={"user": {"sub": "user-123"}})

    response = client.get("/_auth/logout?next=/terms-of-service/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/terms-of-service/"
    assert "expires=Thu, 01 Jan 1970 00:00:00 GMT" in response.headers["set-cookie"]


def test_unauthenticated_internal_auth_file_still_requires_auth(site_dir: Path) -> None:
    protected_auth_file = site_dir / "_auth" / "secret.html"
    protected_auth_file.parent.mkdir(parents=True, exist_ok=True)
    protected_auth_file.write_text("<h1>Protected</h1>", encoding="utf-8")
    client = build_client(site_dir)

    response = client.get("/_auth/secret.html", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/_auth/login?next=%2F_auth%2Fsecret.html"


def test_session_cookie_is_secure_for_https_base_url(site_dir: Path) -> None:
    auth_client = DummyAuthClient()
    client = build_client(site_dir, auth_client=auth_client)

    response = client.get("/_auth/login?next=/", follow_redirects=False)

    assert response.status_code == 307
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "samesite=lax" in response.headers["set-cookie"].lower()
    assert "secure" in response.headers["set-cookie"].lower()


def test_session_cookie_omits_secure_for_local_http(site_dir: Path) -> None:
    auth_client = DummyAuthClient()
    client = build_client(site_dir, public_base_url="http://localhost:8000", auth_client=auth_client)

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
