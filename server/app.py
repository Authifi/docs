from __future__ import annotations

import logging
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from authlib.integrations.starlette_client import OAuth
from starlette.applications import Starlette
from starlette.datastructures import MutableHeaders
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send


logger = logging.getLogger("authifi.docs")

PUBLIC_EXACT_PATHS = {
    "/privacy-policy/",
    "/terms-of-service/",
    "/sms-opt-in.html",
    "/sitemap.xml",
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

SESSION_MAX_AGE_SECONDS = 8 * 60 * 60
DEFAULT_POST_LOGOUT_PATH = "/privacy-policy/"

VISIBILITY_PUBLIC = "public"
VISIBILITY_PROTECTED = "protected"
VISIBILITY_STATE_KEY = "cache_visibility"

PUBLIC_CACHE_CONTROL = "public, max-age=300"
PROTECTED_CACHE_CONTROL = "private, no-store"
REFERRER_POLICY = "strict-origin-when-cross-origin"
HSTS_VALUE = "max-age=63072000; includeSubDomains"

# Content types keyed by canonical request path, for extension-less or
# deliberately overridden resources.
CONTENT_TYPE_BY_PATH = {
    "/.well-known/api-catalog": (
        'application/linkset+json; profile="https://www.rfc-editor.org/info/rfc9727"'
    ),
    "/.well-known/agent-skills/index.json": "application/json",
    "/auth.md": "text/markdown; charset=utf-8",
}

# Explicit table so the served content type never depends on system MIME
# databases, which differ between developer machines and the runtime image.
CONTENT_TYPE_BY_SUFFIX = {
    ".css": "text/css; charset=utf-8",
    ".gif": "image/gif",
    ".gz": "application/gzip",
    ".htm": "text/html; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".ico": "image/x-icon",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".map": "application/json",
    ".md": "text/markdown; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ttf": "font/ttf",
    ".txt": "text/plain; charset=utf-8",
    ".webp": "image/webp",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".xml": "application/xml",
}
FALLBACK_CONTENT_TYPE = "application/octet-stream"

CONTROL_CHARACTERS = frozenset(chr(code) for code in range(0x20)) | {"\x7f"}
DOT_SEGMENTS = frozenset({".", ".."})


@dataclass(frozen=True)
class AppConfig:
    oidc_issuer: str
    oidc_client_id: str
    oidc_client_secret: str
    session_secret: str
    public_base_url: str
    site_dir: Path
    post_logout_path: str = DEFAULT_POST_LOGOUT_PATH
    session_max_age_seconds: int = SESSION_MAX_AGE_SECONDS

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
            post_logout_path=normalize_next_path(
                env.get("POST_LOGOUT_PATH"), default=DEFAULT_POST_LOGOUT_PATH
            ),
        )


class SecurityHeadersMiddleware:
    """Apply baseline security and cache-control headers to every response.

    Responses are classified as public or protected by the endpoint through
    ``request.state``; anything unclassified is treated as protected so a new
    route cannot accidentally become cacheable by a shared cache.
    """

    def __init__(self, app: ASGIApp, enable_hsts: bool = False) -> None:
        self.app = app
        self.enable_hsts = enable_hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("x-content-type-options", "nosniff")
                headers.setdefault("x-frame-options", "DENY")
                headers.setdefault("referrer-policy", REFERRER_POLICY)
                headers.setdefault("vary", "Cookie")
                headers.setdefault("cache-control", cache_control_for(scope))
                if self.enable_hsts:
                    headers.setdefault("strict-transport-security", HSTS_VALUE)
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


def cache_control_for(scope: Scope) -> str:
    visibility = scope.get("state", {}).get(VISIBILITY_STATE_KEY, VISIBILITY_PROTECTED)
    return PUBLIC_CACHE_CONTROL if visibility == VISIBILITY_PUBLIC else PROTECTED_CACHE_CONTROL


def set_cache_visibility(request: Request, visibility: str) -> None:
    setattr(request.state, VISIBILITY_STATE_KEY, visibility)


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
        max_age=config.session_max_age_seconds,
    )
    app.add_middleware(SecurityHeadersMiddleware, enable_hsts=config.cookie_secure)

    if not is_public_path(config.post_logout_path):
        logger.warning(
            "Configured post-logout path %s is not public; users will be sent back to login",
            config.post_logout_path,
        )
    return app


async def health_endpoint(request: Request) -> Response:
    set_cache_visibility(request, VISIBILITY_PROTECTED)
    return JSONResponse({"status": "ok"})


async def login_endpoint(request: Request) -> Response:
    set_cache_visibility(request, VISIBILITY_PROTECTED)
    request.session[SESSION_NEXT_KEY] = normalize_next_path(request.query_params.get("next"))
    redirect_uri = build_public_url(request.app.state.config.public_base_url, "/_auth/callback")
    return await request.app.state.auth_client.authorize_redirect(request, redirect_uri)


async def callback_endpoint(request: Request) -> Response:
    set_cache_visibility(request, VISIBILITY_PROTECTED)
    next_path = normalize_next_path(request.session.pop(SESSION_NEXT_KEY, "/"))
    token = await request.app.state.auth_client.authorize_access_token(request)

    try:
        user = extract_minimal_user((token or {}).get("userinfo"))
    except ValueError as error:
        # Fail closed. The message is deliberately claim-shaped only: it must
        # never carry ID or access token material into logs or the response.
        logger.error("Rejecting Authifi OIDC callback: %s", error)
        request.session.clear()
        return PlainTextResponse(
            "Authentication failed: the identity provider did not return a subject claim.",
            status_code=500,
        )

    request.session.clear()
    request.session[SESSION_USER_KEY] = user
    return RedirectResponse(url=next_path)


async def logout_endpoint(request: Request) -> Response:
    set_cache_visibility(request, VISIBILITY_PROTECTED)
    config = request.app.state.config
    target_path = normalize_next_path(
        request.query_params.get("next"), default=config.post_logout_path
    )

    request.session.clear()

    end_session_endpoint = await discover_end_session_endpoint(request.app.state.auth_client)
    if end_session_endpoint is None:
        return RedirectResponse(url=target_path)

    return RedirectResponse(url=build_end_session_url(end_session_endpoint, config, target_path))


async def site_endpoint(request: Request) -> Response:
    config = request.app.state.config
    set_cache_visibility(request, VISIBILITY_PROTECTED)

    # Read the raw ASGI path rather than request.url.path: URL parsing strips
    # tab and newline characters, which would let the authorization decision
    # and the filesystem lookup disagree about the request.
    canonical_path = canonicalize_request_path(request.scope.get("path", ""))
    if canonical_path is None:
        return PlainTextResponse("Not Found", status_code=404)

    query = safe_query_string(request.scope.get("query_string", b""))

    # A directory page's canonical form is its trailing-slash variant, so that
    # is the path the request is really for and the path authorization must be
    # decided on.
    redirect_target = directory_redirect_target(config.site_dir, canonical_path)
    effective_path = redirect_target or canonical_path
    set_cache_visibility(request, visibility_for(effective_path))

    if not is_public_path(effective_path) and not request.session.get(SESSION_USER_KEY):
        # Answer before redirecting. A 308 here would confirm that a protected
        # directory exists, while a missing path 404s, so the two must look the
        # same to an anonymous caller. `next` echoes the requested path rather
        # than the resolved canonical one for the same reason.
        next_path = quote(build_next_path(canonical_path, query), safe="")
        return RedirectResponse(url=f"/_auth/login?next={next_path}")

    if redirect_target is not None:
        location = f"{redirect_target}?{query}" if query else redirect_target
        return RedirectResponse(url=location, status_code=308)

    resolved_file = resolve_site_file(config.site_dir, canonical_path)
    if resolved_file is None:
        return PlainTextResponse("Not Found", status_code=404)

    response = FileResponse(
        resolved_file,
        media_type=resolve_content_type(canonical_path, resolved_file),
    )
    if canonical_path == "/":
        for link_value in request.app.state.root_links:
            response.headers.append("Link", link_value)
    return response


def visibility_for(canonical_path: str) -> str:
    return VISIBILITY_PUBLIC if is_public_path(canonical_path) else VISIBILITY_PROTECTED


def is_public_path(path: str) -> bool:
    if path == "/health" or path in PUBLIC_AUTH_PATHS:
        return True
    if path in PUBLIC_EXACT_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES)


def canonicalize_request_path(request_path: str) -> str | None:
    """Return the single canonical form of a request path, or ``None``.

    Authorization and file resolution must agree on exactly one string. Rather
    than collapsing ``.``/``..`` (which would let a public prefix carry a
    request into protected content), any path that is not already canonical is
    rejected outright, before the path is classified as public or protected.
    """
    if not request_path.startswith("/"):
        return None
    if "\\" in request_path or "//" in request_path:
        return None
    if any(character in CONTROL_CHARACTERS for character in request_path):
        return None
    if any(segment in DOT_SEGMENTS for segment in request_path.split("/")):
        return None
    return request_path


def build_next_path(canonical_path: str, query: str) -> str:
    return f"{canonical_path}?{query}" if query else canonical_path


def safe_query_string(raw_query_string: bytes) -> str:
    """Decode the query string, dropping it entirely if it is not header-safe."""
    query = raw_query_string.decode("latin-1")
    if any(character in CONTROL_CHARACTERS for character in query):
        return ""
    return query


def normalize_next_path(candidate: str | None, default: str = "/") -> str:
    if not isinstance(candidate, str) or not candidate:
        return default
    if not candidate.startswith("/") or candidate.startswith("//"):
        return default
    if "\\" in candidate:
        return default
    if any(character in CONTROL_CHARACTERS for character in candidate):
        return default
    return candidate


def site_relative_path(canonical_path: str) -> str:
    relative_path = canonical_path.lstrip("/")
    if canonical_path.endswith("/") or not relative_path:
        relative_path = f"{relative_path}index.html"
    return relative_path


def resolve_within_site(site_dir: Path, relative_path: str) -> Path | None:
    site_root = site_dir.resolve()
    candidate = (site_root / relative_path).resolve()
    try:
        candidate.relative_to(site_root)
    except ValueError:
        return None
    return candidate


def resolve_site_file(site_dir: Path, canonical_path: str) -> Path | None:
    candidate = resolve_within_site(site_dir, site_relative_path(canonical_path))
    if candidate is None or not candidate.is_file():
        return None
    return candidate


def directory_redirect_target(site_dir: Path, canonical_path: str) -> str | None:
    """Return the trailing-slash form for an existing directory page."""
    if canonical_path.endswith("/"):
        return None

    candidate = resolve_within_site(site_dir, canonical_path.lstrip("/"))
    if candidate is None or not candidate.is_dir():
        return None
    if not (candidate / "index.html").is_file():
        return None
    return f"{canonical_path}/"


def resolve_content_type(canonical_path: str, resolved_file: Path) -> str:
    override = CONTENT_TYPE_BY_PATH.get(canonical_path)
    if override is not None:
        return override

    known_type = CONTENT_TYPE_BY_SUFFIX.get(resolved_file.suffix.lower())
    if known_type is not None:
        return known_type

    guessed_type, _ = mimetypes.guess_type(resolved_file.name)
    return guessed_type or FALLBACK_CONTENT_TYPE


async def discover_end_session_endpoint(auth_client: object) -> str | None:
    load_server_metadata = getattr(auth_client, "load_server_metadata", None)
    if load_server_metadata is None:
        return None

    try:
        metadata = await load_server_metadata()
    except Exception:
        logger.warning(
            "Could not load Authifi OIDC metadata for RP-initiated logout; "
            "falling back to a local redirect",
            exc_info=True,
        )
        return None

    endpoint = (metadata or {}).get("end_session_endpoint")
    if isinstance(endpoint, str) and endpoint:
        return endpoint
    return None


def build_end_session_url(end_session_endpoint: str, config: AppConfig, target_path: str) -> str:
    parts = urlsplit(end_session_endpoint)
    logout_params = urlencode(
        {
            "post_logout_redirect_uri": build_public_url(config.public_base_url, target_path),
            "client_id": config.oidc_client_id,
        }
    )
    query = f"{parts.query}&{logout_params}" if parts.query else logout_params
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


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
        raise ValueError("OIDC userinfo is missing the required subject claim")

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
