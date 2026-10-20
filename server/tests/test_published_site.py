"""The published site must not carry the infrastructure runbook.

Everything under `docs/` is built, indexed for search, and served to anyone who
can sign in to the tenant -- which in this deployment is authentication only,
so it is every identity the tenant accepts. The AWS and OIDC hosting notes are
not that audience's material: they name the instance's directory layout, the
Systems Manager document, the release bucket, the deploy role's claims, and the
exact commands that stage code onto the host. Published, they are a map for
anyone who gets a session, and a map that stays accurate.

Keeping them out is a build-time property, not an editorial one. A runbook
moved out of `docs/` and back is one commit, a nav entry is one line, and
neither shows up as a broken link or a failed `--strict` build. So this file
builds the site the way CI does and reads the artifacts.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
INTERNAL_OPERATIONS_DOC = ROOT / "operations" / "aws-oidc-hosting.md"

# Strings that characterise the operational material rather than the product
# documentation. Each one is asserted to be present in the internal runbook as
# well as absent from the build, so the list cannot rot into a set of phrases
# that nothing would ever have contained.
OPERATIONAL_MARKERS = (
    "/opt/authifi-docs",
    "journalctl -u authifi-docs",
    "terraform -chdir=infra",
    "DOCS_SSM_DOCUMENT_NAME",
    "DOCS_ALB_DNS_NAME",
    "enable_https_listener",
    "release_bucket_name",
    "workflow_dispatch",
    "Systems Manager",
)


@pytest.fixture(scope="module")
def built_site(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The site exactly as CI builds it, including the search index."""
    site = tmp_path_factory.mktemp("published") / "site"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "mkdocs",
            "build",
            "--strict",
            "--config-file",
            str(ROOT / "mkdocs.yml"),
            "--site-dir",
            str(site),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    return site


def readable_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, ValueError):
        return None


def test_the_markers_really_characterise_the_internal_operations_doc() -> None:
    """Otherwise every absence assertion below is vacuous."""
    text = INTERNAL_OPERATIONS_DOC.read_text(encoding="utf-8")

    for marker in OPERATIONAL_MARKERS:
        assert marker in text, marker


def test_operational_runbooks_live_outside_the_docs_source() -> None:
    """`docs/` is the build's source tree, so anything in it is publishable by
    default and stays unpublished only by a config line someone can delete."""
    assert INTERNAL_OPERATIONS_DOC.is_file()
    assert not (ROOT / "docs" / "operations").exists()


def test_the_navigation_declares_no_operations_section() -> None:
    nav = yaml.safe_load((ROOT / "docs" / ".nav.yml").read_text(encoding="utf-8"))

    assert "operations" not in json.dumps(nav).lower()


def test_the_built_site_publishes_no_operational_pages(built_site: Path) -> None:
    assert not (built_site / "operations").exists()

    sitemap = (built_site / "sitemap.xml").read_text(encoding="utf-8")

    assert "<loc>" in sitemap, "the sitemap is empty; the build produced nothing"
    assert "/operations/" not in sitemap
    assert "superpowers" not in sitemap


def test_the_search_index_carries_no_operational_entries(built_site: Path) -> None:
    """Search is the one place an unlinked page still surfaces: Material ships
    the whole corpus to the browser as one JSON document, so a page absent from
    the nav but present in the index is fully readable to anyone signed in."""
    index = json.loads((built_site / "search" / "search_index.json").read_text(encoding="utf-8"))
    locations = [entry["location"] for entry in index["docs"]]

    assert locations, "the search index is empty; the build produced nothing"
    assert [
        location
        for location in locations
        if location.startswith("operations/") or "superpowers" in location
    ] == []

    corpus = json.dumps(index)
    for marker in OPERATIONAL_MARKERS:
        assert marker not in corpus, marker


def test_no_built_artifact_anywhere_carries_operational_content(built_site: Path) -> None:
    """The whole tree, not the pages the nav happens to name. A stale HTML file
    from a previous layout, a redirect stub, or a partial rendered into every
    page all serve the same content to the same audience.
    """
    offenders = [
        f"{path.relative_to(built_site)}: {marker}"
        for path in sorted(built_site.rglob("*"))
        if path.is_file()
        for text in [readable_text(path)]
        if text is not None
        for marker in OPERATIONAL_MARKERS
        if marker in text
    ]

    assert offenders == []
