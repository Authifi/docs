"""MkDocs hook: agent-readiness assets (sitemap, skill digests, static copies)."""

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


def on_post_build(config, **kwargs) -> None:
    docs_dir = Path(config.docs_dir)
    site_dir = Path(config.site_dir)
    site_url = config.site_url or ""

    _copy_static_files(docs_dir, site_dir)
    _write_sitemap(site_dir, site_url)
    _write_agent_skills_index(site_dir, site_url, docs_dir)
