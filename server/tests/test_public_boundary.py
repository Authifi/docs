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
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import pytest

from server.app import (
    MAX_NEXT_PATH_BYTES,
    PUBLIC_AUTH_PATHS,
    PUBLIC_EXACT_PATHS,
    PUBLIC_PREFIXES,
    is_public_path,
    normalize_next_path,
)

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


# --- Public agent skill must describe the real access model -------------------
#
# This SKILL.md is itself served publicly, under `/.well-known/`, and is written
# to be read by agents deciding what they can fetch. Telling them the whole site
# is public sends them at gated pages and makes the 307 to `/_auth/login` look
# like a bug rather than the boundary working.

OAUTH_SKILL = (
    REPO_ROOT / "docs" / ".well-known" / "agent-skills" / "authifi-oauth-concepts" / "SKILL.md"
)
GATED_SKILL_LINKS = (
    "/authorization/authorization/",
    "/guides/sso-integration-guide/",
    "/authorization/admin-roles/",
    "/guides/nhe-delegated-tokens/",
)


def skill_section(markdown: str, heading: str) -> str:
    body = markdown.split(heading, 1)[1]
    return body.split("\n## ", 1)[0]


def test_the_oauth_skill_never_calls_the_whole_site_public() -> None:
    text = OAUTH_SKILL.read_text(encoding="utf-8")

    assert "is public and unauthenticated" not in text
    assert "mixed access" in text.lower()


def test_the_oauth_skill_lists_exactly_the_server_public_allowlist() -> None:
    section = skill_section(OAUTH_SKILL.read_text(encoding="utf-8"), "## Access model")
    listed = set(re.findall(r"^- `([^`]+)`$", section, flags=re.MULTILINE))

    assert listed == PUBLIC_EXACT_PATHS | set(PUBLIC_PREFIXES)
    for path in listed:
        assert is_public_path(path), path


@pytest.mark.parametrize("path", GATED_SKILL_LINKS)
def test_the_documentation_the_skill_links_to_is_actually_gated(path: str) -> None:
    """If one of these ever becomes public, the caveat below needs revisiting."""
    assert not is_public_path(path)
    assert path in OAUTH_SKILL.read_text(encoding="utf-8")


def test_the_oauth_skill_warns_that_its_links_need_an_interactive_login() -> None:
    text = OAUTH_SKILL.read_text(encoding="utf-8").lower()

    assert "interactive" in text
    assert "sign in" in text or "sign-in" in text or "login" in text


def test_the_oauth_skill_rules_out_an_api_token_bypass() -> None:
    """v1 issues no agent credential, and an agent should not go looking."""
    text = OAUTH_SKILL.read_text(encoding="utf-8").lower()

    assert "api token" in text or "api-token" in text
    assert "v1" in text


def test_the_oauth_skill_denies_an_oauth_server_on_the_docs_domain() -> None:
    text = OAUTH_SKILL.read_text(encoding="utf-8").lower()

    assert "not an oauth authorization server" in text
    assert "token endpoint" in text


# --- Signing out -------------------------------------------------------------
#
# A protected page is reached through an OIDC login, so it needs a way back out
# that does not involve guessing a URL. Public pages have no session to end and
# must not offer one.

LOGOUT_PATH = "/_auth/logout"
LOGOUT_LABEL = "Sign out"

PUBLIC_BUILT_PAGES = (
    "privacy-policy/index.html",
    "terms-of-service/index.html",
    "sms-opt-in.html",
)

# Hand-written HTML copied into the site rather than rendered by Material, so
# there is no header template to hang a link off. `feature-list.html` is gated,
# so the post-build hook adds one to the built artifact instead; `sms-opt-in`
# is public and needs none.
HEADERLESS_BUILT_PAGES = ("feature-list.html", "sms-opt-in.html")
AUGMENTED_BUILT_PAGES = ("feature-list.html",)
SESSION_NAV_SENTINEL = "<!-- authifi:session-nav -->"
UPSTREAM_SOURCE = REPO_ROOT / "docs" / "feature-list.html"


class AnchorCollector(HTMLParser):
    """Anchors matching one href, with the tags open around them and their text."""

    def __init__(self, href: str) -> None:
        super().__init__(convert_charrefs=True)
        self.href = href
        self.open_tags: list[str] = []
        self.matches: list[dict[str, object]] = []
        self._collecting: dict[str, object] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in ("br", "hr", "img", "input", "meta", "link", "source", "path"):
            self.open_tags.append(tag)
        if tag == "a" and dict(attrs).get("href") == self.href:
            self._collecting = {
                "attrs": dict(attrs),
                "ancestors": list(self.open_tags[:-1]),
                "text": "",
            }
            self.matches.append(self._collecting)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._collecting = None
        if tag in self.open_tags:
            del self.open_tags[len(self.open_tags) - 1 - self.open_tags[::-1].index(tag) :]

    def handle_data(self, data: str) -> None:
        if self._collecting is not None:
            self._collecting["text"] = f"{self._collecting['text']}{data}"


def logout_links(html: str) -> list[dict[str, object]]:
    collector = AnchorCollector(LOGOUT_PATH)
    collector.feed(html)
    return collector.matches


def unclosed_tags(fragment: str) -> list[str]:
    collector = AnchorCollector(href="")
    collector.feed(fragment)
    return collector.open_tags


def built_html_pages(built_site: Path) -> list[str]:
    return sorted(path.relative_to(built_site).as_posix() for path in built_site.rglob("*.html"))


def test_every_protected_page_offers_exactly_one_way_out(built_site: Path) -> None:
    protected = [page for page in built_html_pages(built_site) if page not in PUBLIC_BUILT_PAGES]

    assert protected, "no built pages found"
    for page in protected:
        found = logout_links((built_site / page).read_text(encoding="utf-8"))
        assert len(found) == 1, f"{page} has {len(found)} sign-out links"


def test_the_pages_with_no_material_header_are_the_known_ones(built_site: Path) -> None:
    """Copied HTML has no Material header, so a new one must be noticed.

    A gated one needs adding to the hook's list; a public one needs nothing.
    """
    headerless = [
        page
        for page in built_html_pages(built_site)
        if 'class="md-header' not in (built_site / page).read_text(encoding="utf-8")
    ]

    assert sorted(headerless) == sorted(HEADERLESS_BUILT_PAGES)


@pytest.mark.parametrize("page", PUBLIC_BUILT_PAGES)
def test_public_pages_offer_no_way_out_of_a_session_they_never_had(
    built_site: Path, page: str
) -> None:
    html = (built_site / page).read_text(encoding="utf-8")

    assert logout_links(html) == []
    assert LOGOUT_PATH not in html


def test_the_sign_out_link_matches_the_route_the_server_actually_serves() -> None:
    """The template and the server must not drift apart on the path."""
    assert LOGOUT_PATH in PUBLIC_AUTH_PATHS


def test_the_sign_out_link_is_a_plain_link_with_visible_text(built_site: Path) -> None:
    """Native semantics and a name taken from content.

    An icon-only control would need `aria-label`, and a `title` is not a naming
    mechanism at all. Real text is the accessible name, needs no ARIA, and
    survives translation.
    """
    (link,) = logout_links((built_site / "index.html").read_text(encoding="utf-8"))

    assert str(link["text"]).strip() == LOGOUT_LABEL
    assert "aria-label" not in link["attrs"]
    assert "role" not in link["attrs"]
    assert "onclick" not in link["attrs"]


def test_the_sign_out_link_uses_materials_own_header_control_styling(built_site: Path) -> None:
    """`md-header__button` is what the palette toggle and search use.

    Reusing it means the link inherits the header's own foreground colour in
    both the default and slate schemes, and Material's `outline-color` for
    `:focus-visible`, rather than needing contrast and focus styles of its own.
    """
    (link,) = logout_links((built_site / "index.html").read_text(encoding="utf-8"))

    assert "md-header__button" in str(link["attrs"].get("class", ""))


@pytest.mark.parametrize(
    "page", ["index.html", "guides/sso-integration-guide/index.html", "security/index.html"]
)
def test_the_sign_out_link_sits_in_the_header_navigation(built_site: Path, page: str) -> None:
    (link,) = logout_links((built_site / page).read_text(encoding="utf-8"))

    ancestors = link["ancestors"]
    assert "header" in ancestors, ancestors
    assert "nav" in ancestors, ancestors


@pytest.mark.parametrize("page", ["index.html", "privacy-policy/index.html"])
def test_the_header_element_is_still_well_formed(built_site: Path, page: str) -> None:
    """Inserting a link must not leave a tag open or land outside the element."""
    html = (built_site / page).read_text(encoding="utf-8")

    assert html.count("<header") == html.count("</header>") == 1
    header = html[html.index("<header") : html.index("</header>") + len("</header>")]

    assert 'class="md-header__inner md-grid"' in header
    assert unclosed_tags(header) == []


@pytest.mark.parametrize(
    "page", ["index.html", "guides/sso-integration-guide/index.html", "security/index.html"]
)
def test_protected_pages_keep_their_navigation_alongside_the_new_link(
    built_site: Path, page: str
) -> None:
    html = (built_site / page).read_text(encoding="utf-8")

    assert 'data-md-component="sidebar" data-md-type="navigation"' in html
    assert '{% include "partials/nav.html" %}' not in html
    assert 'data-md-component="search"' in html


# --- Vendored header drift ----------------------------------------------------

VENDORED_HEADER = REPO_ROOT / "overrides" / "partials" / "header.html"

# Everything above this line in the vendored file is ours; everything below is
# Material's, transformed only by the two replacements below.
VENDORED_HEADER_SENTINEL = "{# --- vendored mkdocs-material header follows --- #}\n"

MATERIAL_HEADER_SEARCH_CONDITION = '    {% if "material/search" in config.plugins %}\n'
GATED_HEADER_SEARCH_CONDITION = (
    '    {% if "material/search" in config.plugins and not authifi_public_page %}\n'
)

MATERIAL_HEADER_SOURCE_CONDITION = "    {% if config.repo_url %}\n"
SIGN_OUT_LINK = """    {% if not authifi_public_page %}
      <a href="/_auth/logout" class="md-header__button">Sign out</a>
    {% endif %}
"""


def expected_vendored_header() -> str:
    stock = (MATERIAL_TEMPLATES / "partials" / "header.html").read_text(encoding="utf-8")

    gated = stock.replace(MATERIAL_HEADER_SEARCH_CONDITION, GATED_HEADER_SEARCH_CONDITION)
    assert gated != stock, "Material's header search condition changed shape"

    with_link = gated.replace(
        MATERIAL_HEADER_SOURCE_CONDITION, SIGN_OUT_LINK + MATERIAL_HEADER_SOURCE_CONDITION
    )
    assert with_link != gated, "Material's header no longer has the anchor we insert at"
    return with_link


def test_the_vendored_header_is_materials_header_with_only_our_two_changes() -> None:
    """A mkdocs-material upgrade that touches the header must fail here.

    ``overrides/partials/header.html`` shadows the theme's own partial, so it
    has to be re-derived from the installed theme rather than trusted to stay
    current. Deriving the expectation from the installed template is what makes
    silent drift impossible.
    """
    vendored = VENDORED_HEADER.read_text(encoding="utf-8")
    ours, separator, theirs = vendored.partition(VENDORED_HEADER_SENTINEL)

    assert separator, "vendored header is missing its provenance sentinel"
    assert theirs == expected_vendored_header()
    assert "authifi_public_page" in ours, "the public-page flag is not set before it is used"


def test_only_one_copy_of_materials_header_is_vendored() -> None:
    """Two copies would be two things to keep in step with the theme."""
    vendored = sorted(
        path.name
        for path in (REPO_ROOT / "overrides" / "partials").glob("header*.html")
    )

    assert vendored == ["header.html"]


# --- Session navigation on the copied upstream artifact -----------------------


@pytest.mark.parametrize("page", AUGMENTED_BUILT_PAGES)
def test_the_copied_artifact_gains_exactly_one_sign_out_link(built_site: Path, page: str) -> None:
    html = (built_site / page).read_text(encoding="utf-8")

    (link,) = logout_links(html)
    assert str(link["text"]).strip() == LOGOUT_LABEL
    assert "nav" in link["ancestors"], link["ancestors"]


@pytest.mark.parametrize("page", AUGMENTED_BUILT_PAGES)
def test_the_injected_nav_names_itself(built_site: Path, page: str) -> None:
    """Several navigations in one document each need their own name."""
    html = (built_site / page).read_text(encoding="utf-8")

    assert html.count('<nav class="authifi-session-nav" aria-label="Session">') == 1


@pytest.mark.parametrize("page", AUGMENTED_BUILT_PAGES)
def test_the_injected_markup_is_valid_where_it_lands(built_site: Path, page: str) -> None:
    """`<style>` is metadata content: head only. The link is flow content: body."""
    html = (built_site / page).read_text(encoding="utf-8")
    head = html[: html.index("</head>")]
    body = html[html.index("<body>") :]

    assert ".authifi-session-nav" in head
    assert "<style>" not in body
    assert LOGOUT_PATH in body
    assert LOGOUT_PATH not in head
    assert unclosed_tags(body[: body.index("<!-- HERO -->")]) == ["body"]


@pytest.mark.parametrize("page", AUGMENTED_BUILT_PAGES)
def test_the_injected_styling_does_not_lean_on_material(built_site: Path, page: str) -> None:
    """This page never loads Material's stylesheet, so nothing is inherited."""
    html = (built_site / page).read_text(encoding="utf-8")
    style = html[html.index(".authifi-session-nav") :]
    style = style[: style.index("</style>")]

    assert "--md-" not in style
    assert ":focus-visible" in style
    assert "#ffffff" in style


def test_the_upstream_source_is_left_byte_identical(built_site: Path) -> None:
    """The build augments the artifact, never the file idbroker owns."""
    source = UPSTREAM_SOURCE.read_text(encoding="utf-8")

    assert SESSION_NAV_SENTINEL not in source
    assert LOGOUT_PATH not in source

    status = subprocess.run(
        ["git", "status", "--porcelain", "--", str(UPSTREAM_SOURCE)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert status.stdout == "", f"the build modified the upstream source: {status.stdout}"


def run_build(site_dir: Path, *extra: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "mkdocs", "build", "--site-dir", str(site_dir), *extra],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def assert_one_link_per_augmented_page(site_dir: Path) -> None:
    for page in AUGMENTED_BUILT_PAGES:
        html = (site_dir / page).read_text(encoding="utf-8")
        assert len(logout_links(html)) == 1, f"{page} accumulated links across rebuilds"
        # One for the style in the head, one for the nav in the body.
        assert html.count(SESSION_NAV_SENTINEL) == 2


def test_a_strict_rebuild_does_not_stack_two_links(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    site_dir = tmp_path_factory.mktemp("mkdocs-rebuild")

    run_build(site_dir, "--strict")
    run_build(site_dir, "--strict")

    assert_one_link_per_augmented_page(site_dir)


def test_a_dirty_rebuild_over_an_augmented_copy_does_not_stack_two_links(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The case the sentinel exists for.

    A clean build re-copies the artifact, so the injection starts from upstream
    every time. `--dirty` reuses whatever is already in the site directory,
    which is the augmented copy. (It is run without `--strict` because MkDocs
    treats its own "this is a dirty build" warning as fatal under it.)
    """
    site_dir = tmp_path_factory.mktemp("mkdocs-dirty")

    run_build(site_dir, "--strict")
    run_build(site_dir, "--dirty")

    assert_one_link_per_augmented_page(site_dir)


# --- The `next` path cap against the real site --------------------------------
#
# `next` is capped by UTF-8 byte length to bound the signed cookie. The cap is
# only safe if it cannot catch a destination the site actually publishes, so it
# is checked against every built page rather than against a remembered number.

QUERY_HEADROOM_BYTES = 128


def published_request_paths(built_site: Path) -> list[str]:
    paths = []
    for path in built_site.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(built_site).as_posix()
        served = relative.removesuffix("index.html")
        paths.append(f"/{served}")
    return paths


def test_no_path_the_site_publishes_is_anywhere_near_the_cap(built_site: Path) -> None:
    published = published_request_paths(built_site)

    assert published, "no built files found"
    longest = max(published, key=lambda candidate: len(candidate.encode("utf-8")))
    assert len(longest.encode("utf-8")) <= MAX_NEXT_PATH_BYTES - QUERY_HEADROOM_BYTES, longest


def test_every_published_path_survives_normalisation(built_site: Path) -> None:
    """The cap must not be the reason a real page redirects to the root."""
    for path in published_request_paths(built_site):
        assert normalize_next_path(path) == path, path


def test_every_published_path_still_fits_with_a_query_attached(built_site: Path) -> None:
    """Material appends `?h=<terms>` when a search result is followed."""
    highlight = "?h=" + "+".join(["identity"] * 12)
    assert len(highlight.encode("utf-8")) <= QUERY_HEADROOM_BYTES

    for path in published_request_paths(built_site):
        with_query = f"{path}{highlight}"
        assert normalize_next_path(with_query) == with_query, with_query


# --- The navigation skill's access claims -------------------------------------
#
# Both public skills are fetchable anonymously and are read by agents that
# cannot log in. Anything they say about how to reach a page has to be true for
# that reader, or the agent burns requests on a boundary it cannot cross.

NAVIGATION_SKILL = (
    REPO_ROOT / "docs" / ".well-known" / "agent-skills" / "authifi-docs-navigation" / "SKILL.md"
)
PUBLIC_SKILLS = (NAVIGATION_SKILL, OAUTH_SKILL)

# Every product path the navigation skill points an agent at.
NAVIGATION_SKILL_PRODUCT_PATHS = (
    "/",
    "/authorization/",
    "/guides/",
    "/security/",
    "/feature-list.html",
    "/authorization/authorization/",
    "/authorization/admin-roles/",
    "/guides/sso-integration-guide/",
    "/guides/nhe-delegated-tokens/",
    "/security/recommended-secure-configuration/",
)


@pytest.mark.parametrize("skill", PUBLIC_SKILLS, ids=lambda path: path.parent.name)
def test_no_public_skill_promises_markdown_negotiation(skill: Path) -> None:
    """Starlette serves the built file and negotiates nothing.

    `Accept: text/markdown` on a documentation page returns the same HTML, so
    an agent told to ask for Markdown gets HTML and no signal that it asked for
    the wrong thing.
    """
    text = skill.read_text(encoding="utf-8").lower()

    assert "text/markdown" not in text
    assert "markdown for agents" not in text


@pytest.mark.parametrize("skill", PUBLIC_SKILLS, ids=lambda path: path.parent.name)
def test_no_public_skill_calls_the_whole_site_public(skill: Path) -> None:
    text = skill.read_text(encoding="utf-8")

    assert "is public and unauthenticated" not in text
    assert "mixed access" in text.lower()


@pytest.mark.parametrize("skill", PUBLIC_SKILLS, ids=lambda path: path.parent.name)
def test_every_public_skill_lists_exactly_the_server_public_allowlist(skill: Path) -> None:
    section = skill_section(skill.read_text(encoding="utf-8"), "## Access model")
    listed = set(re.findall(r"^- `([^`]+)`$", section, flags=re.MULTILINE))

    assert listed == PUBLIC_EXACT_PATHS | set(PUBLIC_PREFIXES)
    for path in listed:
        assert is_public_path(path), path


@pytest.mark.parametrize("path", NAVIGATION_SKILL_PRODUCT_PATHS)
def test_every_page_the_navigation_skill_points_at_is_gated(path: str) -> None:
    """None of these can be fetched by an agent without a browser session."""
    assert not is_public_path(path)
    assert path in NAVIGATION_SKILL.read_text(encoding="utf-8")


def test_the_navigation_skill_says_its_pages_need_an_interactive_login() -> None:
    text = NAVIGATION_SKILL.read_text(encoding="utf-8").lower()

    assert "interactive" in text
    assert "307" in text, "the redirect an anonymous fetch actually gets is worth naming"


def test_the_navigation_skill_rules_out_an_api_token_bypass() -> None:
    text = NAVIGATION_SKILL.read_text(encoding="utf-8").lower()

    assert "api token" in text or "api-token" in text
    assert "v1" in text


def test_the_navigation_skill_scopes_the_webmcp_tools_to_a_logged_in_browser() -> None:
    """`search_docs` queries a gated index, so it works only after login.

    The tools are also registered only on pages that have the controls they
    drive, which public pages do not; see test_webmcp.
    """
    text = NAVIGATION_SKILL.read_text(encoding="utf-8")

    section = skill_section(text, "## Fetching content")
    assert "search_docs" in section
    assert "browser" in section.lower()
    assert "after" in section.lower() and "sign" in section.lower()


def test_the_navigation_skill_still_names_the_public_discovery_documents() -> None:
    """The one thing an anonymous agent *can* do has to stay findable."""
    text = NAVIGATION_SKILL.read_text(encoding="utf-8")

    for path in ("/.well-known/api-catalog", "/.well-known/agent-skills/index.json", "/robots.txt"):
        assert path in text
        assert is_public_path(path), path
