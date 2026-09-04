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

# A minimal stand-in for the copied upstream artifact.
UPSTREAM_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Upstream</title>
</head>
<body>
<p>Upstream content.</p>
</body>
</html>
"""


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
        "feature-list.html": UPSTREAM_HEAD,
    }.items():
        target = docs_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    # MkDocs copies this one verbatim rather than rendering it.
    (site_dir / "feature-list.html").write_text(UPSTREAM_HEAD, encoding="utf-8")

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


# --- Session navigation injected into copied artifacts ------------------------
#
# `docs/feature-list.html` is generated upstream in idbroker and carries a
# notice not to edit it here, so its source has to stay byte-identical. It is
# also gated, and being a copied file rather than a rendered page it gets none
# of Material's chrome -- including the header sign-out link. The built artifact
# is augmented instead.



def test_the_augmented_artifact_gains_one_session_nav(tmp_path: Path) -> None:
    augmented = agent_assets._with_session_nav(UPSTREAM_HEAD, "feature-list.html")

    assert augmented.count('href="/_auth/logout"') == 1
    assert augmented.count('aria-label="Session"') == 1


def test_the_link_and_its_styling_land_where_they_are_valid() -> None:
    """`<style>` is metadata content, so it belongs in the head, not the body."""
    augmented = agent_assets._with_session_nav(UPSTREAM_HEAD, "feature-list.html")

    head = augmented[: augmented.index("</head>")]
    body = augmented[augmented.index("<body>") :]

    assert "<style>" in head
    assert "<style>" not in body
    assert 'href="/_auth/logout"' in body
    assert 'href="/_auth/logout"' not in head


def test_the_nav_carries_visible_text_and_no_ad_hoc_aria() -> None:
    augmented = agent_assets._with_session_nav(UPSTREAM_HEAD, "feature-list.html")

    assert ">Sign out</a>" in augmented
    assert "aria-label=\"Sign out\"" not in augmented
    assert "role=" not in augmented
    assert "onclick" not in augmented


def test_the_styling_stands_on_its_own() -> None:
    """The page has none of Material's variables, so nothing may be inherited."""
    augmented = agent_assets._with_session_nav(UPSTREAM_HEAD, "feature-list.html")

    style = augmented[augmented.index("<style>") : augmented.index("</style>")]

    assert "--md-" not in style
    assert ":focus-visible" in style
    assert "color:" in style and "background" in style


def test_augmenting_twice_does_not_stack_two_links() -> None:
    """A `--dirty` rebuild can leave an already-augmented copy in place."""
    once = agent_assets._with_session_nav(UPSTREAM_HEAD, "feature-list.html")
    twice = agent_assets._with_session_nav(once, "feature-list.html")

    assert twice == once


@pytest.mark.parametrize(
    "html",
    [
        "<!DOCTYPE html><html><head></head><p>no body tag</p></html>",
        "<!DOCTYPE html><html><body><p>no head close</p></body></html>",
        '<!DOCTYPE html><html><head></head><body class="upstream"></body></html>',
        "<!DOCTYPE html><html><head></head><body></body><body></body></html>",
    ],
)
def test_a_missing_or_ambiguous_insertion_point_fails_the_build(html: str) -> None:
    """Silence here would ship a gated page with no way out of the session."""
    with pytest.raises(RuntimeError, match="feature-list.html"):
        agent_assets._with_session_nav(html, "feature-list.html")


def test_only_the_named_artifacts_are_augmented() -> None:
    """Rendered pages get their link from the header partial instead."""
    assert agent_assets.AUGMENTED_ARTIFACTS == ("feature-list.html",)


def test_post_build_augments_the_artifact_in_the_site_directory(tmp_path: Path) -> None:
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
        path = docs_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    source = docs_dir / "feature-list.html"
    source.write_text(UPSTREAM_HEAD, encoding="utf-8")
    (site_dir / "feature-list.html").write_text(UPSTREAM_HEAD, encoding="utf-8")

    agent_assets.on_post_build(
        SimpleNamespace(
            docs_dir=str(docs_dir), site_dir=str(site_dir), site_url="https://docs.authifi.io"
        )
    )

    assert 'href="/_auth/logout"' in (site_dir / "feature-list.html").read_text(encoding="utf-8")
    assert source.read_text(encoding="utf-8") == UPSTREAM_HEAD


def test_a_listed_artifact_that_was_never_built_fails_the_build(tmp_path: Path) -> None:
    """Losing the page silently would lose its sign-out link with it."""
    with pytest.raises(RuntimeError, match="AUGMENTED_ARTIFACTS"):
        agent_assets._augment_copied_artifacts(tmp_path)
