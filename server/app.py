from __future__ import annotations

import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote

from authlib.integrations.starlette_client import OAuth
from starlette.applications import Starlette
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route


PUBLIC_EXACT_PATHS = {
    "/privacy-policy/",
    "/terms-of-service/",
    "/sms-opt-in.html",
    "/robots.txt",
    "/auth.md",
}
PUBLIC_AUTH_PATHS = {"/_auth/login", "/_auth/callback", "/_auth/logout"}
PUBLIC_PREFIXES = ("/.well-known/", "/assets/", "/javascripts/", "/stylesheets/")
SESSION_COOKIE_NAME = "authifi-session"
DEFAULT_SITE_DIR = "site"
DEFAULT_OIDC_SCOPE = "openid profile email"
SESSION_NEXT_KEY = "next"
SESSION_USER_KEY = "user"


@dataclass(frozen=True)
class AppConfig:
    oidc_issuer: str
    oidc_client_id: str
    oidc_client_secret: str
    session_secret: str
    public_base_url: str
    site_dir: Path

    @property
    def cookie_secure(self) -> bool:
        return self.public_base_url.startswith("https://")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> AppConfig:
        env = dict(os.environ if environ is None else environ)
        base_dir = Path(__file__).resolve().parent.parent
        site_dir_value = env.get("SITE_DIR", DEFAULT_SITE_DIR)
        site_dir = Path(site_dir_value)
        if not site_dir.is_absolute():
            site_dir = (base_dir / site_dir).resolve()

        return cls(
            oidc_issuer=env["OIDC_ISSUER"],
            oidc_client_id=env["OIDC_CLIENT_ID"],
            oidc_client_secret=env["OIDC_CLIENT_SECRET"],
            session_secret=env["SESSION_SECRET"],
            public_base_url=env["PUBLIC_BASE_URL"],
            site_dir=site_dir,
        )


def create_app(config: AppConfig, auth_client: object | None = None) -> Starlette:
    app = Starlette(
        routes=[
            Route("/health", endpoint=health_endpoint),
            Route("/_auth/login", endpoint=login_endpoint),
            Route("/_auth/callback", endpoint=callback_endpoint),
            Route("/_auth/logout", endpoint=logout_endpoint),
            Route("/{path:path}", endpoint=site_endpoint),
        ]
    )
    app.state.config = config
    app.state.auth_client = auth_client or create_auth_client(config)
    app.state.root_links = load_root_links(config.site_dir)
    app.add_middleware(
        SessionMiddleware,
        secret_key=config.session_secret,
        session_cookie=SESSION_COOKIE_NAME,
        same_site="lax",
        https_only=config.cookie_secure,
    )
    return app


async def health_endpoint(request: Request) -> Response:
    return JSONResponse({"status": "ok"})


async def login_endpoint(request: Request) -> Response:
    request.session[SESSION_NEXT_KEY] = normalize_next_path(request.query_params.get("next"))
    redirect_uri = build_public_url(request.app.state.config.public_base_url, "/_auth/callback")
    return await request.app.state.auth_client.authorize_redirect(request, redirect_uri)


async def callback_endpoint(request: Request) -> Response:
    next_path = normalize_next_path(request.session.pop(SESSION_NEXT_KEY, "/"))
    token = await request.app.state.auth_client.authorize_access_token(request)
    request.session.clear()
    request.session[SESSION_USER_KEY] = extract_minimal_user(token.get("userinfo"))
    return RedirectResponse(url=next_path)


async def logout_endpoint(request: Request) -> Response:
    next_path = normalize_next_path(request.query_params.get("next"))
    request.session.clear()
    return RedirectResponse(url=next_path)


async def site_endpoint(request: Request) -> Response:
    if not is_public_path(request.url.path) and not request.session.get(SESSION_USER_KEY):
        next_path = quote(build_next_path(request), safe="")
        return RedirectResponse(url=f"/_auth/login?next={next_path}")

    resolved_path = resolve_site_path(request.app.state.config.site_dir, request.url.path)
    if resolved_path is None or not resolved_path.is_file():
        return PlainTextResponse("Not Found", status_code=404)

    response = Response(content=resolved_path.read_bytes(), media_type=guess_content_type(request.url.path))
    if request.url.path == "/":
        for link_value in request.app.state.root_links:
            response.headers.append("Link", link_value)
    return response


def is_public_path(path: str) -> bool:
    if path == "/health" or path in PUBLIC_AUTH_PATHS:
        return True
    if path in PUBLIC_EXACT_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES)


def build_next_path(request: Request) -> str:
    next_path = request.url.path
    if request.url.query:
        next_path = f"{next_path}?{request.url.query}"
    return next_path


def normalize_next_path(candidate: str | None) -> str:
    if candidate and candidate.startswith("/") and not candidate.startswith("//"):
        return candidate
    return "/"


def resolve_site_path(site_dir: Path, request_path: str) -> Path | None:
    relative_path = request_path.lstrip("/")
    if request_path.endswith("/"):
        relative_path = f"{relative_path}index.html"
    elif not relative_path:
        relative_path = "index.html"

    candidate = (site_dir / relative_path).resolve()
    site_root = site_dir.resolve()
    try:
        candidate.relative_to(site_root)
    except ValueError:
        return None

    if candidate.is_dir():
        candidate = candidate / "index.html"
    return candidate


def guess_content_type(path: str) -> str:
    if path == "/.well-known/api-catalog":
        return 'application/linkset+json; profile="https://www.rfc-editor.org/info/rfc9727"'
    if path == "/.well-known/agent-skills/index.json":
        return "application/json"
    if path == "/auth.md":
        return "text/markdown; charset=utf-8"

    guessed_type, _ = mimetypes.guess_type(path)
    return guessed_type or "application/octet-stream"


def load_root_links(site_dir: Path) -> list[str]:
    headers_path = next((path for path in header_candidates(site_dir) if path.is_file()), None)
    if headers_path is None:
        return []

    lines = headers_path.read_text(encoding="utf-8").splitlines()
    root_links: list[str] = []
    current_section: str | None = None
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if not line.startswith((" ", "\t")):
            current_section = stripped
            continue
        if current_section == "/" and stripped.startswith("Link:"):
            root_links.append(stripped.removeprefix("Link:").strip())
    return root_links


def header_candidates(site_dir: Path) -> tuple[Path, ...]:
    repo_root = Path(__file__).resolve().parent.parent
    return (
        repo_root / "docs" / "_headers",
        site_dir / "_headers",
        repo_root / "site" / "_headers",
    )


def build_public_url(public_base_url: str, path: str) -> str:
    return f"{public_base_url.rstrip('/')}{path}"


def extract_minimal_user(userinfo: Mapping[str, Any] | None) -> dict[str, str]:
    if not userinfo or not userinfo.get("sub"):
        raise ValueError("OIDC userinfo missing required subject")

    user = {"sub": str(userinfo["sub"])}
    for field in ("email", "name"):
        value = userinfo.get(field)
        if value:
            user[field] = str(value)
    return user


def create_auth_client(config: AppConfig):
    oauth = OAuth()
    oauth.register(
        name="authifi",
        client_id=config.oidc_client_id,
        client_secret=config.oidc_client_secret,
        server_metadata_url=build_public_url(
            config.oidc_issuer.rstrip("/"),
            "/.well-known/openid-configuration",
        ),
        client_kwargs={"scope": DEFAULT_OIDC_SCOPE, "code_challenge_method": "S256"},
    )
    return oauth.create_client("authifi")
