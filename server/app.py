from __future__ import annotations

import logging
import mimetypes
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from authlib.integrations.base_client import MismatchingStateError, OAuthError
from authlib.integrations.starlette_client import OAuth, StarletteIntegration
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
# POSIX `NAME_MAX`, in bytes. A longer path component cannot name anything on
# the filesystems this is deployed on, so it is refused before it is probed.
MAX_PATH_SEGMENT_BYTES = 255
# The most a stored return path may weigh, in bytes. `next` is caller-supplied
# and lands in the signed session cookie once per pending login, so an uncapped
# one is a way to inflate that cookie past the 4096 bytes browsers keep --
# after which the browser drops it and the session stops working. Four pending
# logins at this cap measure a little over 3KB of cookie, and the longest path
# the site publishes is 63 bytes, so no real destination comes near it.
# `test_the_session_cookie_stays_under_the_browser_limit` holds that measurement.
MAX_NEXT_PATH_BYTES = 256
SESSION_COOKIE_NAME = "authifi-session"
DEFAULT_SITE_DIR = "site"
DEFAULT_OIDC_SCOPE = "openid profile email"
SESSION_PENDING_LOGINS_KEY = "pending_logins"
SESSION_USER_KEY = "user"

OAUTH_CLIENT_NAME = "authifi"
# Authlib's Starlette integration files each transaction's redirect URI, PKCE
# verifier, and nonce under this prefix, keyed by the OAuth state. Return paths
# are keyed by the same state so the two halves stay in step.
OAUTH_STATE_SESSION_PREFIX = f"_state_{OAUTH_CLIENT_NAME}_"
OAUTH_STATE_BYTES = 32
# An RFC 6749 error code is a short token, but it arrives as a query parameter,
# so it is only fit to appear in a log line if it actually looks like one.
OAUTH_ERROR_CODE = re.compile(r"[a-z0-9_-]{1,64}")

# Anyone can open `/_auth/login`, and every pending transaction costs space in
# a signed cookie the browser will silently drop once it grows past ~4KB. Four
# concurrent tabs is generous for a docs site; older ones are evicted.
MAX_PENDING_LOGINS = 4

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
            # Deliberately unvalidated. Quietly substituting the default here
            # would let a misconfigured deploy start and only misbehave at the
            # first logout; create_app rejects a bad value instead.
            post_logout_path=env.get("POST_LOGOUT_PATH") or DEFAULT_POST_LOGOUT_PATH,
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
                apply_security_headers(
                    MutableHeaders(scope=message),
                    cache_control=cache_control_for(scope),
                    enable_hsts=self.enable_hsts,
                )
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


def apply_security_headers(
    headers: MutableHeaders, cache_control: str, enable_hsts: bool
) -> None:
    """Set the baseline headers, leaving anything a response already chose."""
    headers.setdefault("x-content-type-options", "nosniff")
    headers.setdefault("x-frame-options", "DENY")
    headers.setdefault("referrer-policy", REFERRER_POLICY)
    headers.setdefault("vary", "Cookie")
    headers.setdefault("cache-control", cache_control)
    if enable_hsts:
        headers.setdefault("strict-transport-security", HSTS_VALUE)


def cache_control_for(scope: Scope) -> str:
    visibility = scope.get("state", {}).get(VISIBILITY_STATE_KEY, VISIBILITY_PROTECTED)
    return PUBLIC_CACHE_CONTROL if visibility == VISIBILITY_PUBLIC else PROTECTED_CACHE_CONTROL


def build_server_error_handler(enable_hsts: bool):
    """Return a handler that hardens Starlette's unhandled-exception response.

    Starlette builds that response in ``ServerErrorMiddleware``, which wraps the
    whole application and therefore sits outside ``SecurityHeadersMiddleware``.
    An unhandled exception would otherwise answer with no security headers and
    no cache directive at all, so the headers are applied here instead, through
    the same helper the middleware uses.
    """

    async def handle_server_error(request: Request, exc: Exception) -> Response:
        logger.exception("Unhandled error serving %s", request.scope.get("path", ""))
        response = PlainTextResponse("Internal Server Error", status_code=500)
        apply_security_headers(
            response.headers, cache_control=PROTECTED_CACHE_CONTROL, enable_hsts=enable_hsts
        )
        return response

    return handle_server_error


def set_cache_visibility(request: Request, visibility: str) -> None:
    setattr(request.state, VISIBILITY_STATE_KEY, visibility)


def create_app(config: AppConfig, auth_client: object | None = None) -> Starlette:
    validate_post_logout_path(config.post_logout_path)

    app = Starlette(
        routes=[
            Route("/health", endpoint=health_endpoint),
            Route("/_auth/login", endpoint=login_endpoint),
            Route("/_auth/callback", endpoint=callback_endpoint),
            Route("/_auth/logout", endpoint=logout_endpoint),
            Route("/{path:path}", endpoint=site_endpoint),
        ],
        # Passed to the constructor rather than added afterwards so the handler
        # is guaranteed to be in place before the middleware stack is built.
        exception_handlers={Exception: build_server_error_handler(config.cookie_secure)},
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
    return app


def validate_post_logout_path(path: str) -> str:
    """Reject a post-logout landing page that users could not actually reach.

    Checked against the exact public paths rather than ``is_public_path``: the
    public *prefixes* cover stylesheets, scripts, and well-known documents, none
    of which are a page to land on. A protected value would bounce every
    logged-out user straight back into a login, and an unsafe one would be
    handed to the issuer as ``post_logout_redirect_uri``, so both fail at
    startup instead of at the first logout.
    """
    if not path or normalize_next_path(path, default="") != path:
        raise ValueError(
            f"POST_LOGOUT_PATH must be a safe site-relative path, got {path!r}"
        )
    if path not in PUBLIC_EXACT_PATHS:
        allowed = ", ".join(sorted(PUBLIC_EXACT_PATHS))
        raise ValueError(
            f"POST_LOGOUT_PATH must be one of the publicly served pages ({allowed}), got {path!r}"
        )
    return path


# What has to be on disk for this process to be worth routing traffic to: the
# front page, and the page every logout lands on -- which is also the compliance
# document that has to stay publicly reachable. A runtime stage that shipped
# without its built site answers `404` to everything, and reporting that as
# healthy is how it becomes the live deployment. Written out rather than derived
# so the list is reviewable; a test holds it equal to the configured target.
HEALTH_REQUIRED_ARTIFACTS = ("index.html", "privacy-policy/index.html")


def artifact_is_readable(path: Path) -> bool:
    """Whether the process can actually read something from `path`.

    Opening it rather than asking `Path.is_file()`: a file with no read
    permission for this uid, or a directory wearing the right name, both pass
    an existence check and then fail every request. A zero-byte read is a
    failed build too, so emptiness counts as absence.
    """
    try:
        with path.open("rb") as artifact:
            return bool(artifact.read(1))
    except OSError:
        return False


def unhealthy_site_artifacts(config: AppConfig) -> list[str]:
    """The required artifacts this process cannot serve, for the log."""
    required = dict.fromkeys((*HEALTH_REQUIRED_ARTIFACTS, site_relative_path(config.post_logout_path)))
    return [
        relative_path
        for relative_path in required
        if not artifact_is_readable(config.site_dir / relative_path)
    ]


async def health_endpoint(request: Request) -> Response:
    set_cache_visibility(request, VISIBILITY_PROTECTED)

    unhealthy = unhealthy_site_artifacts(request.app.state.config)
    if unhealthy:
        # The detail goes to the log, not the response: this endpoint is served
        # to anyone, and the layout of the image is not theirs to learn.
        logger.error("Health check failed; unreadable site artifacts: %s", ", ".join(unhealthy))
        return JSONResponse({"status": "unavailable"}, status_code=503)

    return JSONResponse({"status": "ok"})


def oauth_state_session_key(state: str) -> str:
    return f"{OAUTH_STATE_SESSION_PREFIX}{state}"


def pending_logins(session: Mapping[str, Any]) -> dict[str, str]:
    stored = session.get(SESSION_PENDING_LOGINS_KEY)
    return dict(stored) if isinstance(stored, dict) else {}


def remember_pending_login(session: Any, state: str, next_path: str) -> None:
    """File this login's return path under its own OAuth state.

    A single ``next`` slot meant a second tab overwrote the first one's
    destination, so whichever login finished last decided where both went.
    """
    pending = pending_logins(session)
    pending[state] = next_path

    # Dicts keep insertion order, so the oldest transaction is evicted first.
    while len(pending) > MAX_PENDING_LOGINS:
        pending.pop(next(iter(pending)))

    # Authlib's half of an evicted transaction is the bulky half, and it would
    # otherwise linger in the cookie for the full hour until its own expiry.
    for key in [key for key in session if key.startswith(OAUTH_STATE_SESSION_PREFIX)]:
        if key.removeprefix(OAUTH_STATE_SESSION_PREFIX) not in pending:
            session.pop(key)

    session[SESSION_PENDING_LOGINS_KEY] = pending


def consume_pending_login(session: Any, state: str | None) -> str | None:
    """Take this state's return path, leaving every other transaction intact."""
    if not state:
        return None

    pending = pending_logins(session)
    next_path = pending.pop(state, None)
    if next_path is None:
        return None

    session[SESSION_PENDING_LOGINS_KEY] = pending
    return next_path


def reset_session_preserving_pending_logins(session: Any) -> None:
    """Start a fresh session, keeping only still-pending login transactions.

    Clearing outright is the session-fixation defence, and it has to stay: a
    pre-existing ``user`` must not survive a callback. It cannot take the other
    tabs' in-flight OAuth transactions with it, though, so those are carried
    over and nothing else is.
    """
    pending = pending_logins(session)
    preserved: dict[str, Any] = {}
    if pending:
        preserved[SESSION_PENDING_LOGINS_KEY] = pending
        for state in pending:
            key = oauth_state_session_key(state)
            if key in session:
                preserved[key] = session[key]

    session.clear()
    session.update(preserved)


async def login_endpoint(request: Request) -> Response:
    set_cache_visibility(request, VISIBILITY_PROTECTED)
    # Generated here rather than left to Authlib so the return path can be
    # stored under the same key Authlib uses for its verifier and nonce.
    state = secrets.token_urlsafe(OAUTH_STATE_BYTES)
    remember_pending_login(
        request.session, state, normalize_next_path(request.query_params.get("next"))
    )
    redirect_uri = build_public_url(request.app.state.config.public_base_url, "/_auth/callback")
    return await request.app.state.auth_client.authorize_redirect(
        request, redirect_uri, state=state
    )


def unrecognised_login_response() -> Response:
    """One answer for every callback we cannot match to a live transaction.

    Unknown, forged, replayed, and aged-out states are deliberately
    indistinguishable to the caller, and none of them reach the issuer.
    """
    return PlainTextResponse(
        "Authentication failed: this sign-in request is unknown or has expired. "
        "Start again from the page you wanted.",
        status_code=400,
    )


def refused_login_response(session: dict[str, Any], state: str, error: OAuthError) -> Response:
    """Report a sign-in the issuer declined, without repeating anything it said.

    Authlib raises `OAuthError` both for an authorization response carrying
    `error=...` -- an ordinary outcome, the user pressed "no" -- and for a state
    that no longer matches its own record. Either way this sign-in is over, and
    neither means the process is broken.
    """
    # Authlib drops its own record when the state mismatches, but not when it
    # rejects the authorization response, which it does before reading the state
    # at all. Dropping it here leaves no transaction behind that can never
    # complete, and reaches no other tab's.
    session.pop(oauth_state_session_key(state), None)

    if isinstance(error, MismatchingStateError):
        # Our pending entry outlived Authlib's. Authlib stamps each stored
        # transaction with an hour's expiry and sweeps the expired ones the next
        # time any callback completes, so a tab left open long enough clears our
        # gate and then finds its verifier already gone. Nothing was exchanged
        # with the issuer, so answer exactly as for a state we never issued.
        logger.warning("Rejecting Authifi OIDC callback whose stored transaction has expired")
        return unrecognised_login_response()

    # Neither the error code nor its description is trustworthy: both arrive as
    # query parameters. The code reaches the log only in the shape it is meant
    # to have, the free-text description never does, and nothing from the issuer
    # reaches the response at all.
    code = getattr(error, "error", None)
    recognised = isinstance(code, str) and OAUTH_ERROR_CODE.fullmatch(code)
    logger.warning(
        "Authifi OIDC issuer declined the authorization: %s",
        code if recognised else "unrecognised error",
    )
    return PlainTextResponse(
        "Authentication failed: the identity provider did not complete this sign-in. "
        "Start again from the page you wanted.",
        status_code=400,
    )


async def callback_endpoint(request: Request) -> Response:
    set_cache_visibility(request, VISIBILITY_PROTECTED)

    state = request.query_params.get("state")
    next_path = consume_pending_login(request.session, state)
    if next_path is None:
        # No pending transaction under that state: already used, or never ours.
        # Refuse before exchanging anything, and leave the other tabs'
        # transactions alone so a forged callback cannot cancel them.
        logger.warning("Rejecting Authifi OIDC callback with an unrecognised state")
        return unrecognised_login_response()

    try:
        token = await request.app.state.auth_client.authorize_access_token(request)
    except OAuthError as error:
        # Authlib's own error type and no wider: an unreachable issuer or a
        # programming fault is not an authentication outcome and must not be
        # presented to the caller as one.
        return refused_login_response(request.session, state, error)

    try:
        user = extract_minimal_user((token or {}).get("userinfo"))
    except ValueError as error:
        # Fail closed. The message is deliberately claim-shaped only: it must
        # never carry ID or access token material into logs or the response.
        logger.error("Rejecting Authifi OIDC callback: %s", error)
        reset_session_preserving_pending_logins(request.session)
        return PlainTextResponse(
            "Authentication failed: the identity provider did not return a subject claim.",
            status_code=500,
        )

    reset_session_preserving_pending_logins(request.session)
    request.session[SESSION_USER_KEY] = user
    return RedirectResponse(url=normalize_next_path(next_path))


async def logout_endpoint(request: Request) -> Response:
    set_cache_visibility(request, VISIBILITY_PROTECTED)
    config = request.app.state.config

    # `next` is deliberately ignored. The post-logout target is registered with
    # Authifi as an exact URI, so anything else would be rejected by the issuer,
    # and the local fallback matches it so both flows land in the same place.
    was_signed_in = bool(request.session.get(SESSION_USER_KEY))
    request.session.clear()

    if not was_signed_in:
        # Nothing to end at the issuer, and an anonymous caller must not be able
        # to drive outbound metadata requests by hitting this route in a loop.
        return RedirectResponse(url=config.post_logout_path)

    end_session_endpoint = await discover_end_session_endpoint(request.app.state.auth_client)
    if end_session_endpoint is None:
        return RedirectResponse(url=config.post_logout_path)

    return RedirectResponse(
        url=build_end_session_url(end_session_endpoint, config, config.post_logout_path)
    )


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
        # A public prefix makes the *content* cacheable, not its absence. A
        # shared cache holding a 404 for /assets/app.<hash>.css would outlive
        # the deploy that adds the file.
        set_cache_visibility(request, VISIBILITY_PROTECTED)
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


def segment_is_nameable(segment: str) -> bool:
    """Whether one path component could name a file at all.

    `NAME_MAX` is 255 *bytes* on every filesystem this is deployed on, so a
    longer component cannot exist and is not worth asking the kernel about. It
    is worth screening: on the pinned runtime `Path.is_dir` lets ENAMETOOLONG
    through -- pathlib absorbs ENOENT and ENOTDIR, not this -- and that probe
    runs before the authorization decision, so any anonymous caller could turn
    a long URL into a 500 and a log line.
    """
    try:
        return len(segment.encode("utf-8")) <= MAX_PATH_SEGMENT_BYTES
    except UnicodeError:
        # A path the server could not even re-encode is not one we can serve.
        return False


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
    segments = request_path.split("/")
    if any(segment in DOT_SEGMENTS for segment in segments):
        return None
    if not all(segment_is_nameable(segment) for segment in segments):
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
    try:
        # Weighed first, so the scans below are bounded work on a caller-supplied
        # string, and because bytes are the unit that matters to the cookie: 100
        # characters of Japanese are 300 bytes of it.
        oversized = len(candidate.encode("utf-8")) > MAX_NEXT_PATH_BYTES
    except UnicodeError:
        # No UTF-8 form, so no byte length, and nothing that could be stored or
        # redirected to either.
        return default
    if oversized:
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
    try:
        candidate = (site_root / relative_path).resolve()
        candidate.relative_to(site_root)
    except (OSError, ValueError):
        # `PATH_MAX` bounds the whole path, not just one component, so a legal
        # path of legal segments can still be refused. Nothing to serve either
        # way; see `is_existing_file`.
        return None
    return candidate


def is_existing_file(path: Path) -> bool:
    """`Path.is_file`, reading a refusal from the kernel as "no".

    The per-segment screen in `canonicalize_request_path` keeps the common case
    away from the filesystem, but this backstop is what makes the answer
    independent of which errnos the running pathlib happens to absorb. A
    refused stat and a missing file are the same answer to a request: 404.
    """
    try:
        return path.is_file()
    except OSError:
        return False


def is_existing_directory(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def resolve_site_file(site_dir: Path, canonical_path: str) -> Path | None:
    candidate = resolve_within_site(site_dir, site_relative_path(canonical_path))
    if candidate is None or not is_existing_file(candidate):
        return None
    return candidate


def directory_redirect_target(site_dir: Path, canonical_path: str) -> str | None:
    """Return the trailing-slash form for an existing directory page."""
    if canonical_path.endswith("/"):
        return None

    candidate = resolve_within_site(site_dir, canonical_path.lstrip("/"))
    if candidate is None or not is_existing_directory(candidate):
        return None
    if not is_existing_file(candidate / "index.html"):
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


class BoundedStateStorage(StarletteIntegration):
    """Keep several in-flight OAuth transactions, not just the newest one.

    Authlib's Starlette integration deletes every stored transaction each time
    a new one starts, to stop the signed cookie growing without bound. The side
    effect is that opening a second login tab destroys the first tab's PKCE
    verifier and nonce, so its callback can never complete. Bound the store by
    count instead of emptying it, which keeps the cookie small and lets
    concurrent logins finish in any order.
    """

    async def set_state_data(self, session, state, data) -> None:
        if isinstance(data, dict) and "url" in data:
            # The full authorization URL is by far the largest field in a
            # transaction and is never read back out of the session: the
            # redirect is built from the return value, and the callback needs
            # only the redirect URI, PKCE verifier, and nonce. Dropping it is
            # what makes several concurrent logins fit in one signed cookie.
            data = {key: value for key, value in data.items() if key != "url"}

        prefix = f"_state_{self.name}_"
        preserved = (
            {key: value for key, value in session.items() if key.startswith(prefix)}
            if session is not None
            else {}
        )

        await super().set_state_data(session, state, data)

        if session is None:
            return

        # super() emptied the store and wrote this transaction. Reinsert the
        # most recent others ahead of it, so insertion order still tracks age.
        new_key = f"{prefix}{state}"
        new_value = session.pop(new_key)
        for key, value in list(preserved.items())[-(MAX_PENDING_LOGINS - 1) :]:
            if key != new_key:
                session[key] = value
        session[new_key] = new_value


class DocsOAuth(OAuth):
    framework_integration_cls = BoundedStateStorage


def create_auth_client(config: AppConfig):
    oauth = DocsOAuth()
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
