"""MkDocs hook: agent-readiness assets (sitemap, skill digests, static copies).

The hook is also the single source of truth for which built pages are served
without authentication, so the published sitemap and the rendered navigation
cannot drift apart from each other.
"""

import gzip
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urljoin
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
    "privacy-policy.md",
    "terms-of-service.md",
    "sms-opt-in.html",
)

# Markdown pages that anonymous visitors can reach. Their rendered navigation
# would otherwise advertise every protected guide, authorization, and security
# page by title and URL, and their search box would query a protected index.
PUBLIC_PAGE_SOURCES = (
    "privacy-policy.md",
    "terms-of-service.md",
)

# "navigation" is Material's own page metadata. "search" is consumed by
# overrides/partials/header.html, which omits the search control and the
# sign-out link on those pages.
PUBLIC_PAGE_HIDDEN_CHROME = ("navigation", "search")

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


def _write_sitemap(site_dir: Path, site_url: str) -> None:
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


def on_page_markdown(markdown, page, config, files, **kwargs):
    """Hide protected chrome from pages that anonymous visitors can read."""
    if page.file.src_uri in PUBLIC_PAGE_SOURCES:
        hidden = list(page.meta.get("hide") or [])
        for item in PUBLIC_PAGE_HIDDEN_CHROME:
            if item not in hidden:
                hidden.append(item)
        page.meta["hide"] = hidden
    return markdown


def on_post_build(config, **kwargs) -> None:
    docs_dir = Path(config.docs_dir)
    site_dir = Path(config.site_dir)
    site_url = config.site_url or ""

    _copy_static_files(docs_dir, site_dir)
    _augment_copied_artifacts(site_dir)
    _write_sitemap(site_dir, site_url)
    _write_agent_skills_index(site_dir, site_url, docs_dir)
