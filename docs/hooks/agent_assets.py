"""MkDocs hook: agent-readiness assets (sitemap, skill digests, static copies).

The hook is also the single source of truth for which built pages are served
without authentication, so the published sitemap and the rendered navigation
cannot drift apart from each other.

A page opts out of the login gate with `public: true` in its frontmatter. The
build records that page, and the images it embeds, in
`.well-known/public-paths.json`. The server reads that file at startup.

Anonymous readers get that page without the navigation, search box, or
previous/next links, since those name guides that still require a login. A
reader who signs in is served a second copy of the same page rendered with the
full site chrome, kept under `SIGNED_IN_DIR`. The server picks between the two
by session, so the reader's URL does not change.
"""

import gzip
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from xml.dom import minidom

SKILL_DEFINITIONS = (
    {
        "name": "authifi-docs-navigation",
        "type": "skill-md",
        "description": (
            "Navigate the Authifi documentation site information architecture "
            "and find relevant guides."
        ),
        "path": ".well-known/agent-skills/authifi-docs-navigation/SKILL.md",
    },
    {
        "name": "authifi-oauth-concepts",
        "type": "skill-md",
        "description": (
            "Understand Authifi OAuth 2.0 and OIDC concepts as documented, "
            "without calling live product APIs."
        ),
        "path": ".well-known/agent-skills/authifi-oauth-concepts/SKILL.md",
    },
)

STATIC_COPIES = (
    "auth.md",
    ".well-known/api-catalog",
    ".well-known/agent-skills/authifi-docs-navigation/SKILL.md",
    ".well-known/agent-skills/authifi-oauth-concepts/SKILL.md",
)
PUBLIC_SITEMAP_PATHS = (
    "logged-off.md",
    "privacy-policy.md",
    "terms-of-service.md",
    "sms-opt-in.html",
)

# Markdown pages that anonymous visitors can reach. Their rendered navigation
# would otherwise advertise every protected guide, authorization, and security
# page by title and URL, and their search box would query a protected index.
PUBLIC_PAGE_SOURCES = (
    "logged-off.md",
    "privacy-policy.md",
    "terms-of-service.md",
)

# "navigation" is Material's own page metadata. "search" is consumed by
# overrides/partials/header.html, which omits the search control and the
# sign-out link on those pages.
PUBLIC_PAGE_HIDDEN_CHROME = ("navigation", "search")

# Written into the built site. `/.well-known/` is already served without a
# login, so the manifest needs no new allowlist entry. The server unions these
# paths into the paths it serves anonymously.
PUBLIC_CONTENT_MANIFEST = ".well-known/public-paths.json"

# Where the full-chrome copy of each `public: true` page is written. It is not
# a public path, so an anonymous request for it is sent to the login page, and
# the server only ever answers a public page's own URL with it for a session.
SIGNED_IN_DIR = "_signed-in"

# Image embeds on a public page have to be fetchable too, or the page is broken
# for the anonymous reader it was opened for. Scripts and stylesheets are left
# out: a `src` that is not an image can point at the protected search worker.
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".avif"}
IMG_SRC = re.compile(r"""(?i)\bsrc\s*=\s*["']([^"']+)["']""")

# Pages whose frontmatter set `public: true`, keyed by source path so a rebuild
# that renders a page twice does not list it twice. `on_pre_build` clears it.
_public_content_pages: dict[str, object] = {}

# What each public page's `hide` list was before the hook added the public
# chrome to it, and the page rendered with that original list. Same keys and
# lifetime as `_public_content_pages`.
_original_hide: dict[str, list[str]] = {}
_signed_in_html: dict[str, str] = {}

# Gated HTML that MkDocs copies verbatim instead of rendering, so it gets none
# of Material's chrome and no sign-out link from the header partial. These are
# generated upstream in idbroker and carry a notice not to edit them here, so
# the built artifact is augmented and the source is left alone.
AUGMENTED_ARTIFACTS = ("feature-list.html",)

SESSION_NAV_SENTINEL = "<!-- authifi:session-nav -->"

# The page has none of Material's custom properties, so every colour here is
# literal. White on this slate is about 16:1, well past the 4.5:1 wanted for
# text, and the underline means the link is identifiable without relying on
# colour at all.
SESSION_NAV_STYLE = f"""{SESSION_NAV_SENTINEL}
<style>
.authifi-session-nav {{
  background: #0f172a;
  padding: 0.75rem 1.5rem;
  text-align: right;
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  font-size: 1rem;
}}
.authifi-session-nav form {{
  display: inline;
  margin: 0;
}}
.authifi-session-nav button {{
  background: none;
  border: 0;
  cursor: pointer;
  font: inherit;
  color: #ffffff;
  font-weight: 600;
  text-decoration: underline;
  padding: 0.5rem 0.75rem;
  border-radius: 0.25rem;
}}
.authifi-session-nav button:hover {{
  background: #1e293b;
}}
.authifi-session-nav button:focus-visible {{
  outline: 3px solid #ffffff;
  outline-offset: 2px;
}}
</style>
"""

# A form, not a link: `/_auth/logout` changes state and answers `POST` only.
SESSION_NAV_MARKUP = f"""{SESSION_NAV_SENTINEL}
<nav class="authifi-session-nav" aria-label="Session">
  <form method="post" action="/_auth/logout">
    <button type="submit">Sign out</button>
  </form>
</nav>
"""

# `<style>` is metadata content, so it goes in the head and the link in the
# body. Both have to be there exactly once: no match means the upstream file
# changed shape, and two would make the insertion ambiguous.
SESSION_NAV_INSERTION_POINTS = ("</head>", "<body>")


def _sha256_digest(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"sha256:{digest}"


def _page_to_url(site_url: str, page: str) -> str:
    if page == "index.md":
        return site_url.rstrip("/") + "/"

    if page.endswith(".html"):
        return urljoin(site_url, page)

    slug = page.removesuffix(".md")
    return urljoin(site_url, f"{slug}/")


def _write_sitemap(
    site_dir: Path, site_url: str, extra_page_paths: tuple[str, ...] = ()
) -> None:
    urlset = ET.Element(
        "urlset", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
    )

    seen: set[str] = set()
    for page in PUBLIC_SITEMAP_PATHS:
        loc = _page_to_url(site_url, page)
        if loc in seen:
            continue
        seen.add(loc)
        url = ET.SubElement(urlset, "url")
        ET.SubElement(url, "loc").text = loc

    # Content pages follow the fixed legal pages, in path order, so a rebuild
    # does not reshuffle the sitemap because a page was rendered first.
    for path in extra_page_paths:
        loc = urljoin(site_url.rstrip("/") + "/", path.lstrip("/"))
        if loc in seen:
            continue
        seen.add(loc)
        url = ET.SubElement(urlset, "url")
        ET.SubElement(url, "loc").text = loc

    xml_body = ET.tostring(urlset, encoding="unicode")
    pretty = minidom.parseString(xml_body).toprettyxml(indent="  ")
    sitemap_path = site_dir / "sitemap.xml"
    sitemap_path.write_text(pretty, encoding="utf-8")

    # MkDocs writes its own sitemap.xml.gz covering every built page, including
    # gated ones. Overwrite it from the public sitemap so the compressed copy
    # can never advertise protected URLs.
    gzip_path = site_dir / "sitemap.xml.gz"
    with gzip.GzipFile(gzip_path, "wb", mtime=0) as compressed:
        compressed.write(pretty.encode("utf-8"))


def _write_agent_skills_index(site_dir: Path, site_url: str, docs_dir: Path) -> None:
    skills: list[dict[str, str]] = []

    for definition in SKILL_DEFINITIONS:
        source_path = docs_dir / definition["path"]
        site_path = site_dir / definition["path"]
        skills.append(
            {
                "name": definition["name"],
                "type": definition["type"],
                "description": definition["description"],
                "url": urljoin(site_url, definition["path"]),
                "digest": _sha256_digest(site_path if site_path.exists() else source_path),
            }
        )

    index = {
        "$schema": "https://schemas.agentskills.io/discovery/0.2.0/schema.json",
        "skills": skills,
    }

    index_json = json.dumps(index, indent=2) + "\n"
    site_index = site_dir / ".well-known" / "agent-skills" / "index.json"
    site_index.parent.mkdir(parents=True, exist_ok=True)
    site_index.write_text(index_json, encoding="utf-8")

    source_index = docs_dir / ".well-known" / "agent-skills" / "index.json"
    source_index.write_text(index_json, encoding="utf-8")


def _copy_static_files(docs_dir: Path, site_dir: Path) -> None:
    for relative_path in STATIC_COPIES:
        source = docs_dir / relative_path
        target = site_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())


def _with_session_nav(html: str, label: str) -> str:
    """Return ``html`` with a sign-out link, adding it at most once.

    Idempotent because a `--dirty` rebuild can leave an already-augmented copy
    in the site directory, and two sign-out links would be two controls with the
    same accessible name. Loud on a missing or ambiguous insertion point: the
    upstream file is not ours to depend on, and quietly doing nothing would ship
    a gated page with no way out of the session.
    """
    if SESSION_NAV_SENTINEL in html:
        return html

    for insertion_point in SESSION_NAV_INSERTION_POINTS:
        found = html.count(insertion_point)
        if found != 1:
            raise RuntimeError(
                f"{label}: expected exactly one {insertion_point!r} to attach the "
                f"session navigation to, found {found}. The upstream copy has "
                "changed shape; update docs/hooks/agent_assets.py to match."
            )

    head_close, body_open = SESSION_NAV_INSERTION_POINTS
    html = html.replace(head_close, SESSION_NAV_STYLE + head_close, 1)
    return html.replace(body_open, f"{body_open}\n{SESSION_NAV_MARKUP}", 1)


def _augment_copied_artifacts(site_dir: Path) -> None:
    for relative_path in AUGMENTED_ARTIFACTS:
        target = site_dir / relative_path
        if not target.is_file():
            raise RuntimeError(
                f"{relative_path} is listed in AUGMENTED_ARTIFACTS but was not built. "
                "Remove it from that list if the page is gone; a gated page with no "
                "sign-out link must not ship by accident."
            )
        augmented = _with_session_nav(target.read_text(encoding="utf-8"), relative_path)
        target.write_text(augmented, encoding="utf-8")


def on_pre_build(config, **kwargs) -> None:
    """Drop pages remembered from an earlier build in this process."""
    _public_content_pages.clear()
    _original_hide.clear()
    _signed_in_html.clear()


def _hide_protected_chrome(page) -> None:
    hidden = list(page.meta.get("hide") or [])
    for item in PUBLIC_PAGE_HIDDEN_CHROME:
        if item not in hidden:
            hidden.append(item)
    page.meta["hide"] = hidden


def _page_url_path(page) -> str:
    """Site path of a rendered page, with the leading slash MkDocs omits."""
    url = getattr(page.file, "url", None)
    if not isinstance(url, str) or not url:
        raise RuntimeError(
            f"{page.file.src_uri} sets public: true but MkDocs gave it no URL"
        )
    if not url.startswith("/"):
        url = "/" + url
    return url


def _image_paths(page_path: str, html: str, site_url: str) -> list[str]:
    """Same-origin image `src` values on a public page.

    Resolved against the page so a relative screenshot link becomes the path
    the browser requests. A link (`href`) to another guide is not included: that
    page stays behind the login unless it opts in itself.
    """
    base = site_url.rstrip("/") + page_path
    origin = urlsplit(site_url)
    found: list[str] = []
    for raw in IMG_SRC.findall(html):
        parts = urlsplit(urljoin(base, raw))
        if parts.scheme and (
            parts.scheme != origin.scheme or parts.netloc != origin.netloc
        ):
            continue
        path = parts.path
        if Path(path).suffix.lower() not in IMAGE_SUFFIXES:
            continue
        if ".." in path.split("/"):
            continue
        found.append(path)
    return found


def _built_file(site_dir: Path, path: str) -> Path | None:
    """The file a site path names, or None when it escapes the build directory."""
    relative = path.lstrip("/")
    if path.endswith("/"):
        relative = f"{relative}index.html"
    site_root = site_dir.resolve()
    try:
        candidate = (site_root / relative).resolve()
        candidate.relative_to(site_root)
    except (OSError, ValueError):
        return None
    return candidate


def _write_public_content_manifest(site_dir: Path, site_url: str) -> tuple[str, ...]:
    """Record public content pages and the images they embed.

    Returns the page paths, in order, for the sitemap. The manifest itself also
    lists the images, which are not pages and so stay out of the sitemap.
    """
    paths: set[str] = set()
    page_paths: list[str] = []
    for page in _public_content_pages.values():
        url_path = _page_url_path(page)
        built = _built_file(site_dir, url_path)
        if built is None or not built.is_file():
            raise RuntimeError(
                f"{url_path} is marked public but {built} is not in the build"
            )
        paths.add(url_path)
        page_paths.append(url_path)
        html = built.read_text(encoding="utf-8")
        for image in _image_paths(url_path, html, site_url):
            image_file = _built_file(site_dir, image)
            if image_file is None or not image_file.is_file():
                raise RuntimeError(
                    f"{url_path} embeds {image}, which was not built. A public "
                    "page can only embed an image the build actually produced."
                )
            paths.add(image)

    manifest = site_dir / PUBLIC_CONTENT_MANIFEST
    manifest.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(paths)
    manifest.write_text(
        json.dumps({"paths": ordered}, indent=2) + "\n", encoding="utf-8"
    )
    return tuple(sorted(set(page_paths)))


def _write_signed_in_pages(site_dir: Path) -> None:
    """Write the full-chrome copy of every public page under SIGNED_IN_DIR."""
    for src_uri, page in _public_content_pages.items():
        html = _signed_in_html.get(src_uri)
        if html is None:
            raise RuntimeError(
                f"{src_uri} is marked public but has no signed-in copy; "
                "on_page_context did not run for it."
            )
        target = _built_file(site_dir, f"/{SIGNED_IN_DIR}{_page_url_path(page)}")
        if target is None:
            raise RuntimeError(f"{src_uri} has no safe signed-in path")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html, encoding="utf-8")


def _extend_robots_allows(robots_text: str, paths: tuple[str, ...]) -> str:
    """Add Allow lines to every user-agent that already publishes some.

    A block with only `Disallow: /` is a crawler that is turned away entirely.
    Those blocks are left alone. New lines go after the block's last Allow, so
    they stay with the other grants and ahead of Content-Signal.
    """
    if not paths:
        return robots_text

    lines = robots_text.splitlines()
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.lower().startswith("user-agent:") and current:
            blocks.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append(current)

    rewritten: list[str] = []
    for block in blocks:
        allow_at = [
            index
            for index, line in enumerate(block)
            if line.lower().startswith("allow:")
        ]
        if allow_at:
            existing = {block[index].split(":", 1)[1].strip() for index in allow_at}
            additions = [f"Allow: {path}" for path in paths if path not in existing]
            insert_at = allow_at[-1] + 1
            block = block[:insert_at] + additions + block[insert_at:]
        rewritten.extend(block)

    trailing = "\n" if robots_text.endswith("\n") else ""
    return "\n".join(rewritten) + trailing


def _render_signed_in_page(context, page, config) -> str:
    """The page as a signed-in reader gets it: navigation, search, prev/next.

    Rendered here, while MkDocs has the page active and the context built,
    with the page's own `hide` list put back for the duration.
    """
    chrome_hide = page.meta.get("hide")
    page.meta["hide"] = _original_hide.get(page.file.src_uri, [])
    try:
        template = config.theme.get_env().get_template(
            page.meta.get("template", "main.html")
        )
        return template.render(dict(context))
    finally:
        page.meta["hide"] = chrome_hide


def on_page_context(context, page, config, nav, **kwargs):
    """Drop the previous and next links on a public page.

    Those point at the neighboring entries in the nav, which are gated guides.
    The links are in the document head, so hiding the sidebar does not remove
    them, and a public page must not advertise a URL that still requires login.
    The signed-in copy is rendered first, while those links are still set.
    """
    if page.meta.get("public") is True:
        _signed_in_html[page.file.src_uri] = _render_signed_in_page(
            context, page, config
        )
        page.previous_page = None
        page.next_page = None
    return context


def on_page_markdown(markdown, page, config, files, **kwargs):
    """Hide protected chrome from pages that anonymous visitors can read."""
    # `public: true` is how a content page opts in. The fixed legal pages stay
    # listed in PUBLIC_PAGE_SOURCES because they predate that flag.
    if page.meta.get("public") is True:
        _original_hide[page.file.src_uri] = list(page.meta.get("hide") or [])
        _public_content_pages[page.file.src_uri] = page
    if page.file.src_uri in PUBLIC_PAGE_SOURCES or page.meta.get("public") is True:
        _hide_protected_chrome(page)
    return markdown


def on_post_build(config, **kwargs) -> None:
    docs_dir = Path(config.docs_dir)
    site_dir = Path(config.site_dir)
    site_url = config.site_url or ""

    _copy_static_files(docs_dir, site_dir)
    _augment_copied_artifacts(site_dir)
    page_paths = _write_public_content_manifest(site_dir, site_url)
    _write_signed_in_pages(site_dir)
    _write_sitemap(site_dir, site_url, page_paths)
    manifest = json.loads(
        (site_dir / PUBLIC_CONTENT_MANIFEST).read_text(encoding="utf-8")
    )
    robots = site_dir / "robots.txt"
    if robots.is_file():
        robots.write_text(
            _extend_robots_allows(robots.read_text(encoding="utf-8"), tuple(manifest["paths"])),
            encoding="utf-8",
        )
    _write_agent_skills_index(site_dir, site_url, docs_dir)
