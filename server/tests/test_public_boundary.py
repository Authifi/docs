"""Built-artifact tests for the public/protected boundary.

These run a real ``mkdocs build --strict`` and inspect the output, so a
regression in navigation rendering, sitemap generation, or the documented
allowlist fails here instead of in production.
"""

from __future__ import annotations

import gzip
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import pytest

from server.app import PUBLIC_EXACT_PATHS, PUBLIC_PREFIXES, is_public_path

REPO_ROOT = Path(__file__).resolve().parents[2]
SITE_URL = "https://docs.authifi.io"
SITEMAP_NAMESPACE = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

PUBLIC_PAGE_URLS = {
    "privacy-policy/index.html": f"{SITE_URL}/privacy-policy/",
    "terms-of-service/index.html": f"{SITE_URL}/terms-of-service/",
}

PROTECTED_CONTENT_PREFIXES = (
    "/authorization/",
    "/guides/",
    "/operations/",
    "/search/",
    "/security/",
)
PROTECTED_CONTENT_PATHS = ("/feature-list.html",)

# Multi-word navigation titles that cannot plausibly occur in legal copy.
PROTECTED_NAV_TITLES = (
    "Tenant Administration",
    "NHE Delegated Tokens",
    "FedRAMP Compliance Evidence",
    "Recommended Secure Configuration",
    "Privileged Access Summary",
    "Trusted Tenant Implementation",
    "Delegating Tenant Management",
    "Default Application User Groups",
)

LINK_ATTRIBUTE_PATTERN = re.compile(r'(?:href|src)="([^"]+)"')


@pytest.fixture(scope="module")
def built_site(tmp_path_factory: pytest.TempPathFactory) -> Path:
    site_dir = tmp_path_factory.mktemp("mkdocs-site")
    subprocess.run(
        [sys.executable, "-m", "mkdocs", "build", "--strict", "--site-dir", str(site_dir)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    return site_dir


def resolved_link_paths(html: str, page_url: str) -> Iterator[str]:
    for value in LINK_ATTRIBUTE_PATTERN.findall(html):
        absolute = urljoin(page_url, value)
        if absolute.startswith(SITE_URL):
            yield urlsplit(absolute).path


def is_protected_content_path(path: str) -> bool:
    return path in PROTECTED_CONTENT_PATHS or path.startswith(PROTECTED_CONTENT_PREFIXES)


# --- Public pages must not advertise protected content ------------------------


@pytest.mark.parametrize("relative_path", sorted(PUBLIC_PAGE_URLS))
def test_public_pages_link_to_no_protected_content(built_site: Path, relative_path: str) -> None:
    html = (built_site / relative_path).read_text(encoding="utf-8")

    leaked = sorted(
        {path for path in resolved_link_paths(html, PUBLIC_PAGE_URLS[relative_path]) if is_protected_content_path(path)}
    )

    assert leaked == []


@pytest.mark.parametrize("relative_path", sorted(PUBLIC_PAGE_URLS))
def test_public_pages_omit_protected_navigation_markup(built_site: Path, relative_path: str) -> None:
    html = (built_site / relative_path).read_text(encoding="utf-8")

    assert 'data-md-type="navigation"' not in html
    assert "md-sidebar--primary" not in html


@pytest.mark.parametrize("relative_path", sorted(PUBLIC_PAGE_URLS))
@pytest.mark.parametrize("title", PROTECTED_NAV_TITLES)
def test_public_pages_omit_protected_navigation_titles(
    built_site: Path, relative_path: str, title: str
) -> None:
    html = (built_site / relative_path).read_text(encoding="utf-8")

    assert title not in html


@pytest.mark.parametrize("relative_path", sorted(PUBLIC_PAGE_URLS))
def test_public_pages_keep_material_styling_and_content(built_site: Path, relative_path: str) -> None:
    html = (built_site / relative_path).read_text(encoding="utf-8")

    assert "assets/stylesheets/main." in html
    assert 'class="md-content__inner md-typeset"' in html
    assert 'data-md-type="toc"' in html
    assert f'<link rel="canonical" href="{PUBLIC_PAGE_URLS[relative_path]}">' in html


def test_protected_pages_still_render_navigation(built_site: Path) -> None:
    html = (built_site / "guides" / "sso-integration-guide" / "index.html").read_text(encoding="utf-8")

    assert 'data-md-type="navigation"' in html
    assert "Tenant Administration" in html


# --- Search control -----------------------------------------------------------
#
# The search index lives at the protected /search/ prefix, so a search box on a
# public page can only ever fail. Material starts the search worker (and fetches
# the index) only when a form named "search" is in the document, so the markup
# check below is also the check that no failing request is issued.

MATERIAL_TEMPLATES = Path(__import__("material").__file__).resolve().parent / "templates"

MATERIAL_HEADER_SEARCH_BLOCK = """    {% if "material/search" in config.plugins %}
      {% set search = config.plugins["material/search"] | attr("config") %}
      {% if search.enabled %}
        <label class="md-header__button md-icon" for="__search">
          {% set icon = config.theme.icon.search or "material/magnify" %}
          {% include ".icons/" ~ icon ~ ".svg" %}
        </label>
        {% include "partials/search.html" %}
      {% endif %}
    {% endif %}
"""

SEARCH_CONTROL_MARKERS = (
    'data-md-component="search"',
    'name="search"',
    'data-md-component="search-query"',
    'for="__search"',
)


@pytest.mark.parametrize("relative_path", sorted(PUBLIC_PAGE_URLS))
@pytest.mark.parametrize("marker", SEARCH_CONTROL_MARKERS)
def test_public_pages_omit_the_search_control(built_site: Path, relative_path: str, marker: str) -> None:
    html = (built_site / relative_path).read_text(encoding="utf-8")

    assert marker not in html


@pytest.mark.parametrize("relative_path", sorted(PUBLIC_PAGE_URLS))
def test_public_pages_do_not_reference_the_protected_search_index(
    built_site: Path, relative_path: str
) -> None:
    html = (built_site / relative_path).read_text(encoding="utf-8")

    assert "search_index" not in html
    assert not any(
        path.startswith("/search/") for path in resolved_link_paths(html, PUBLIC_PAGE_URLS[relative_path])
    )


@pytest.mark.parametrize(
    "relative_path",
    ["index.html", "guides/sso-integration-guide/index.html", "security/index.html"],
)
@pytest.mark.parametrize("marker", SEARCH_CONTROL_MARKERS)
def test_protected_pages_keep_the_search_control(built_site: Path, relative_path: str, marker: str) -> None:
    html = (built_site / relative_path).read_text(encoding="utf-8")

    assert marker in html


def test_protected_pages_keep_the_search_index(built_site: Path) -> None:
    assert (built_site / "search" / "search_index.json").is_file()
    assert not is_public_path("/search/search_index.json")


# --- Navigation override drift ------------------------------------------------
#
# `overrides/main.html` re-implements Material's `site_nav` block rather than
# copying it, because Material's version always emits the primary sidebar and
# only marks it `hidden`, which still ships every protected title and URL. A
# byte-for-byte comparison is therefore impossible, so the guard is the other
# direction: pin the upstream block we wrote the override against.

MATERIAL_SITE_NAV_BLOCK = """{% block site_nav %}
            {% if nav %}
              {% if page.meta and page.meta.hide %}
                {% set hidden = "hidden" if "navigation" in page.meta.hide %}
              {% endif %}
              <div class="md-sidebar md-sidebar--primary" data-md-component="sidebar" \
data-md-type="navigation" {{ hidden }}>
                <div class="md-sidebar__scrollwrap">
                  <div class="md-sidebar__inner">
                    {% include "partials/nav.html" %}
                  </div>
                </div>
              </div>
            {% endif %}
            {% if "toc.integrate" not in features %}
              {% if page.meta and page.meta.hide %}
                {% set hidden = "hidden" if "toc" in page.meta.hide %}
              {% endif %}
              <div class="md-sidebar md-sidebar--secondary" data-md-component="sidebar" \
data-md-type="toc" {{ hidden }}>
                <div class="md-sidebar__scrollwrap">
                  <div class="md-sidebar__inner">
                    {% include "partials/toc.html" %}
                  </div>
                </div>
              </div>
            {% endif %}
          {% endblock %}"""


def extract_jinja_block(template: str, name: str) -> str:
    start = template.index("{%% block %s %%}" % name)
    end = template.index("{% endblock %}", start) + len("{% endblock %}")
    return template[start:end]


def collapse_whitespace(markup: str) -> str:
    return re.sub(r"\s+", " ", markup).strip()


def test_material_site_nav_block_is_the_one_the_override_was_written_against() -> None:
    """Fails on a mkdocs-material upgrade that reshapes navigation rendering.

    The override cannot be re-derived automatically the way the header copy can,
    so an upgrade has to be reviewed by hand. This is what forces that review.
    """
    base_template = (MATERIAL_TEMPLATES / "base.html").read_text(encoding="utf-8")

    installed = extract_jinja_block(base_template, "site_nav")

    assert installed == MATERIAL_SITE_NAV_BLOCK


def test_site_nav_override_makes_the_primary_sidebar_conditional() -> None:
    override = extract_jinja_block(
        (REPO_ROOT / "overrides" / "main.html").read_text(encoding="utf-8"), "site_nav"
    )

    assert "{% if nav and not hide_navigation %}" in override
    # Material's `hidden` attribute on the primary sidebar is the bug, not the fix.
    assert 'data-md-type="navigation" {{ hidden }}' not in override


def test_site_nav_override_keeps_materials_secondary_sidebar() -> None:
    """The table of contents must render exactly as Material renders it."""
    override = collapse_whitespace(
        extract_jinja_block((REPO_ROOT / "overrides" / "main.html").read_text(encoding="utf-8"), "site_nav")
    )

    for fragment in (
        '{% if "toc.integrate" not in features %}',
        'class="md-sidebar md-sidebar--secondary" data-md-component="sidebar" data-md-type="toc"',
        '{% include "partials/toc.html" %}',
    ):
        assert collapse_whitespace(fragment) in override


def test_public_header_is_the_material_header_minus_search() -> None:
    """A mkdocs-material upgrade that touches the header must fail here.

    ``overrides/partials/header-public.html`` is a verbatim copy of Material's
    header with exactly one block removed. Re-deriving it from the installed
    theme means the vendored copy cannot drift silently.
    """
    material_header = (MATERIAL_TEMPLATES / "partials" / "header.html").read_text(encoding="utf-8")
    expected = material_header.replace(MATERIAL_HEADER_SEARCH_BLOCK, "")
    assert expected != material_header, "Material's header search block changed shape"

    vendored = (REPO_ROOT / "overrides" / "partials" / "header-public.html").read_text(encoding="utf-8")
    _, separator, body = vendored.partition("-#}\n")

    assert separator, "vendored header is missing its provenance comment"
    assert body == expected


# --- Generated sitemaps -------------------------------------------------------


def sitemap_locations(sitemap_xml: str) -> list[str]:
    root = ET.fromstring(sitemap_xml)
    return [node.text or "" for node in root.findall("sm:url/sm:loc", SITEMAP_NAMESPACE)]


def test_sitemap_lists_only_public_urls(built_site: Path) -> None:
    locations = sitemap_locations((built_site / "sitemap.xml").read_text(encoding="utf-8"))

    assert locations == [
        f"{SITE_URL}/privacy-policy/",
        f"{SITE_URL}/terms-of-service/",
        f"{SITE_URL}/sms-opt-in.html",
    ]


def test_gzipped_sitemap_cannot_contain_gated_urls(built_site: Path) -> None:
    gzipped = gzip.decompress((built_site / "sitemap.xml.gz").read_bytes()).decode("utf-8")

    assert sitemap_locations(gzipped) == sitemap_locations(
        (built_site / "sitemap.xml").read_text(encoding="utf-8")
    )
    for path in PROTECTED_CONTENT_PREFIXES:
        assert path not in gzipped


def test_every_sitemap_url_is_served_publicly(built_site: Path) -> None:
    for location in sitemap_locations((built_site / "sitemap.xml").read_text(encoding="utf-8")):
        path = urlsplit(location).path
        assert is_public_path(path), f"sitemap advertises non-public path {path}"


def test_hook_source_is_not_published(built_site: Path) -> None:
    assert not (built_site / "hooks").exists()
    assert list(built_site.rglob("agent_assets.py")) == []
    assert list(built_site.rglob("*.pyc")) == []


# --- Allowlist drift ----------------------------------------------------------


def documented_public_paths(auth_markdown: str) -> set[str]:
    section = auth_markdown.split("intentionally public:", 1)[1].split("\n\n##", 1)[0]
    return set(re.findall(r"^- `([^`]+)`$", section, flags=re.MULTILINE))


def robots_allow_blocks(robots_text: str) -> dict[str, set[str]]:
    blocks: dict[str, set[str]] = {}
    current_agent: str | None = None
    for line in robots_text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("user-agent:"):
            current_agent = stripped.split(":", 1)[1].strip()
            blocks.setdefault(current_agent, set())
        elif stripped.lower().startswith("allow:") and current_agent is not None:
            blocks[current_agent].add(stripped.split(":", 1)[1].strip())
    return blocks


def test_auth_md_documents_exactly_the_server_allowlist() -> None:
    documented = documented_public_paths((REPO_ROOT / "docs" / "auth.md").read_text(encoding="utf-8"))

    assert documented == PUBLIC_EXACT_PATHS | set(PUBLIC_PREFIXES)


def test_robots_allow_lists_match_the_server_allowlist(built_site: Path) -> None:
    robots_text = (built_site / "robots.txt").read_text(encoding="utf-8")
    expected = PUBLIC_EXACT_PATHS | set(PUBLIC_PREFIXES)

    blocks = robots_allow_blocks(robots_text)

    assert blocks, "robots.txt declared no user agents"
    for agent, allowed in blocks.items():
        # Blanket-blocked crawlers intentionally have no Allow lines at all.
        if not allowed:
            continue
        assert allowed == expected, f"robots.txt Allow list for {agent} drifted from the server allowlist"


def terraform_post_logout_variable() -> str:
    """Return the `post_logout_path` variable block from infra/variables.tf."""
    terraform = (REPO_ROOT / "infra" / "variables.tf").read_text(encoding="utf-8")
    start = terraform.index('variable "post_logout_path"')
    end = terraform.index('\nvariable "', start + 1) if '\nvariable "' in terraform[start + 1 :] else len(terraform)
    return terraform[start:end]


def test_terraform_post_logout_allowlist_matches_the_server_allowlist() -> None:
    """Terraform cannot import server/app.py, so the HCL copy is checked instead.

    Without this, a new public page would be accepted by the server and rejected
    at plan time, or worse, accepted by Terraform and rejected at startup after
    the image is already rolling out.
    """
    block = terraform_post_logout_variable()
    contains_list = block.split("contains(", 1)[1].split("]", 1)[0]

    declared = set(re.findall(r'"([^"]+)"', contains_list))

    assert declared == PUBLIC_EXACT_PATHS


def test_terraform_rejects_post_logout_paths_the_server_would_reject() -> None:
    """Both format guards must survive, since only one gives a useful message.

    A control character can never appear in the exact allowlist, so validation
    three already rejects it; validation two exists so the operator is told the
    value is malformed rather than merely unlisted.
    """
    block = terraform_post_logout_variable()

    assert 'startswith(var.post_logout_path, "/")' in block
    assert '!startswith(var.post_logout_path, "//")' in block
    assert "[:cntrl:]" in block
    assert block.count("validation {") == 3


def test_terraform_default_post_logout_path_is_a_public_page() -> None:
    block = terraform_post_logout_variable()

    default = re.search(r'default\s+=\s+"([^"]+)"', block)

    assert default is not None
    assert default.group(1) in PUBLIC_EXACT_PATHS


def test_robots_advertises_the_public_sitemap(built_site: Path) -> None:
    robots_text = (built_site / "robots.txt").read_text(encoding="utf-8")

    assert f"Sitemap: {SITE_URL}/sitemap.xml" in robots_text
    assert is_public_path("/sitemap.xml")


@pytest.mark.parametrize("path", sorted(PUBLIC_EXACT_PATHS))
def test_every_public_exact_path_exists_in_the_build(built_site: Path, path: str) -> None:
    relative = path.lstrip("/")
    target = built_site / (f"{relative}index.html" if path.endswith("/") else relative)

    assert target.is_file(), f"{path} is allowlisted but missing from the build"


def test_public_prefixes_are_not_required_to_appear_in_the_sitemap(built_site: Path) -> None:
    """Serving a path publicly and listing it in the sitemap are separate things."""
    locations = sitemap_locations((built_site / "sitemap.xml").read_text(encoding="utf-8"))
    sitemap_paths = {urlsplit(location).path for location in locations}

    assert not any(path.startswith(PUBLIC_PREFIXES) for path in sitemap_paths)
    assert (built_site / "assets").is_dir()
