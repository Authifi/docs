from __future__ import annotations

import gzip
import importlib.util
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "docs" / "hooks" / "agent_assets.py"
SPEC = importlib.util.spec_from_file_location("agent_assets", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
agent_assets = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent_assets)


def test_write_sitemap_includes_only_public_content(tmp_path: Path) -> None:
    agent_assets._write_sitemap(tmp_path, "https://docs.authifi.io")

    sitemap = ET.fromstring((tmp_path / "sitemap.xml").read_text(encoding="utf-8"))
    namespace = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls = [node.text for node in sitemap.findall("sm:url/sm:loc", namespace)]
    assert urls == [
        "https://docs.authifi.io/privacy-policy/",
        "https://docs.authifi.io/terms-of-service/",
        "https://docs.authifi.io/sms-opt-in.html",
    ]


def test_post_build_does_not_require_site_headers_file(tmp_path: Path) -> None:
    docs_dir = tmp_path / "docs"
    site_dir = tmp_path / "site"
    docs_dir.mkdir()
    site_dir.mkdir()

    for relative_path, content in {
        "auth.md": "# Auth\n",
        ".well-known/api-catalog": '{"links":[]}\n',
        ".well-known/agent-skills/authifi-docs-navigation/SKILL.md": "# Skill\n",
        ".well-known/agent-skills/authifi-oauth-concepts/SKILL.md": "# Skill\n",
    }.items():
        target = docs_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    config = SimpleNamespace(
        docs_dir=str(docs_dir),
        site_dir=str(site_dir),
        site_url="https://docs.authifi.io",
    )

    agent_assets.on_post_build(config)

    assert (site_dir / "sitemap.xml").exists()
    assert not (site_dir / "_headers").exists()


def test_write_sitemap_replaces_the_default_gzipped_sitemap(tmp_path: Path) -> None:
    stale = tmp_path / "sitemap.xml.gz"
    stale.write_bytes(gzip.compress(b"<urlset><url><loc>https://docs.authifi.io/guides/x/</loc></url></urlset>"))

    agent_assets._write_sitemap(tmp_path, "https://docs.authifi.io")

    regenerated = gzip.decompress(stale.read_bytes()).decode("utf-8")
    assert "guides" not in regenerated
    assert regenerated == (tmp_path / "sitemap.xml").read_text(encoding="utf-8")


def build_page(src_uri: str, meta: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(file=SimpleNamespace(src_uri=src_uri), meta=meta if meta is not None else {})


@pytest.mark.parametrize("src_uri", agent_assets.PUBLIC_PAGE_SOURCES)
def test_public_pages_are_marked_to_hide_protected_chrome(src_uri: str) -> None:
    page = build_page(src_uri, {"title": "Privacy Policy"})

    returned = agent_assets.on_page_markdown("# Body", page, config=None, files=None)

    assert page.meta["hide"] == ["navigation", "search"]
    assert returned == "# Body"


@pytest.mark.parametrize("already_hidden", [["navigation"], ["search"], ["navigation", "search"]])
def test_public_page_hide_metadata_is_not_duplicated(already_hidden: list[str]) -> None:
    page = build_page("privacy-policy.md", {"hide": list(already_hidden)})

    agent_assets.on_page_markdown("# Body", page, config=None, files=None)

    assert sorted(page.meta["hide"]) == ["navigation", "search"]


def test_public_page_hide_metadata_preserves_existing_values() -> None:
    page = build_page("terms-of-service.md", {"hide": ["toc"]})

    agent_assets.on_page_markdown("# Body", page, config=None, files=None)

    assert page.meta["hide"] == ["toc", "navigation", "search"]


@pytest.mark.parametrize("src_uri", ["index.md", "guides/sso-integration-guide.md", "security/README.md"])
def test_protected_pages_keep_their_navigation_and_search(src_uri: str) -> None:
    page = build_page(src_uri)

    agent_assets.on_page_markdown("# Body", page, config=None, files=None)

    assert "hide" not in page.meta


def test_public_page_sources_are_a_subset_of_the_public_sitemap() -> None:
    assert set(agent_assets.PUBLIC_PAGE_SOURCES) <= set(agent_assets.PUBLIC_SITEMAP_PATHS)


# --- Committed skills index must match the committed skills -------------------


SKILLS_DIR = REPO_ROOT / "docs" / ".well-known" / "agent-skills"


def committed_index() -> dict:
    return json.loads((SKILLS_DIR / "index.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("definition", agent_assets.SKILL_DEFINITIONS, ids=lambda d: d["name"])
def test_committed_index_digest_matches_the_committed_skill(definition: dict) -> None:
    """The digest is what an agent uses to trust the file it fetched.

    `on_post_build` rewrites `docs/.well-known/agent-skills/index.json` in
    place, so editing a SKILL.md without rebuilding publishes a digest for the
    previous text.
    """
    entry = next(item for item in committed_index()["skills"] if item["name"] == definition["name"])

    assert entry["digest"] == agent_assets._sha256_digest(REPO_ROOT / "docs" / definition["path"])


@pytest.mark.parametrize("definition", agent_assets.SKILL_DEFINITIONS, ids=lambda d: d["name"])
def test_committed_index_description_matches_the_hook(definition: dict) -> None:
    entry = next(item for item in committed_index()["skills"] if item["name"] == definition["name"])

    assert entry["description"] == definition["description"]


def test_committed_index_covers_every_defined_skill() -> None:
    names = {entry["name"] for entry in committed_index()["skills"]}

    assert names == {definition["name"] for definition in agent_assets.SKILL_DEFINITIONS}
