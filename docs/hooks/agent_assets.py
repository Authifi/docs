"""MkDocs hook: agent-readiness assets (sitemap, skill digests, static copies)."""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
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

# Navigation is derived by the awesome-nav plugin and no longer lives in
# mkdocs.yml (config.nav is None at post-build time). Capture the resolved
# Navigation in on_nav so the sitemap can still enumerate pages in nav order.
_resolved_nav: Any = None


def _sha256_digest(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"sha256:{digest}"


def _iter_nav_pages(nav: Any, pages: list[str]) -> None:
    # Resolved mkdocs Navigation object (from the awesome-nav plugin).
    if hasattr(nav, "items"):
        for item in nav.items:
            _iter_nav_pages(item, pages)
        return

    # StructureItem: Section, Page or Link.
    if hasattr(nav, "is_section"):
        if nav.is_section:
            for child in nav.children:
                _iter_nav_pages(child, pages)
        elif nav.is_page:
            pages.append(nav.url)
        elif nav.is_link:
            pages.append(nav.url)
        return

    # Raw mkdocs.yml nav config (fallback when no plugin rewrites the nav).
    if isinstance(nav, str):
        pages.append(nav)
        return

    if isinstance(nav, dict):
        for value in nav.values():
            _iter_nav_pages(value, pages)
        return

    if isinstance(nav, list):
        for item in nav:
            _iter_nav_pages(item, pages)


def _page_to_url(site_url: str, page: str) -> str:
    base_url = site_url.rstrip("/") + "/"
    if page.startswith(("/", "#")) or "://" in page:
        return urljoin(base_url, page)
    if page in ("", "index.md", "README.md"):
        return base_url
    if page.endswith(("/index.md", "/README.md")):
        page = page.rsplit("/", 1)[0] + "/"
    elif page.endswith(".md"):
        page = page.removesuffix(".md") + "/"
    return urljoin(base_url, page)


def _write_sitemap(site_dir: Path, site_url: str, nav: Any) -> None:
    pages: list[str] = []
    _iter_nav_pages(nav, pages)

    urlset = ET.Element(
        "urlset", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
    )

    seen: set[str] = set()
    for page in pages:
        loc = _page_to_url(site_url, page)
        if loc in seen:
            continue
        seen.add(loc)
        url = ET.SubElement(urlset, "url")
        ET.SubElement(url, "loc").text = loc

    xml_body = ET.tostring(urlset, encoding="unicode")
    pretty = minidom.parseString(xml_body).toprettyxml(indent="  ")
    lines = [line for line in pretty.splitlines() if line.strip()]
    sitemap_path = site_dir / "sitemap.xml"
    sitemap_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


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


def on_nav(nav, config, files):
    global _resolved_nav
    _resolved_nav = nav
    return nav


def on_post_build(config, **kwargs) -> None:
    docs_dir = Path(config.docs_dir)
    site_dir = Path(config.site_dir)
    site_url = config.site_url or ""

    nav = _resolved_nav if _resolved_nav is not None else config.nav

    _copy_static_files(docs_dir, site_dir)
    _write_sitemap(site_dir, site_url, nav)
    _write_agent_skills_index(site_dir, site_url, docs_dir)

    headers_path = site_dir / "_headers"
    if not headers_path.exists():
        raise FileNotFoundError(
            "Expected Cloudflare _headers file at site/_headers after build."
        )
