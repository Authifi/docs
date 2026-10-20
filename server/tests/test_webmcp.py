"""Behaviour tests for the WebMCP tool registrations.

The script runs in the browser, so these load it in Node against a fake DOM
shaped like the two kinds of page this site serves: a protected page with
Material's search control and primary navigation, and a public page where
`overrides/main.html` strips both. A tool that cannot work on the page it is
registered on is worse than no tool, because an agent will call it and act on
the error.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WEBMCP_SOURCE = REPO_ROOT / "docs" / "javascripts" / "webmcp.js"

requires_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not available")

DRIVER = """
const fs = require("fs");
const vm = require("vm");

const scenario = JSON.parse(process.env.WEBMCP_SCENARIO);

const searchInput = {
  focus() {},
  value: "",
  dispatchEvent() { this.dispatched = true; },
};
const sectionLinks = [
  { textContent: "  Guides  ", getAttribute: () => "/guides/" },
  { textContent: "Security", getAttribute: () => "/security/" },
];

const document = {
  querySelector(selector) {
    if (selector.includes("search")) {
      return scenario.hasSearch ? searchInput : null;
    }
    return null;
  },
  querySelectorAll(selector) {
    if (selector.includes("md-nav--primary") || selector.includes("md-tabs__link")) {
      return scenario.hasNav ? sectionLinks : [];
    }
    return [];
  },
};

const registered = [];
Object.defineProperty(globalThis, "navigator", {
  value: {
    modelContext: {
      registerTool(descriptor) { registered.push(descriptor); },
    },
  },
  configurable: true,
  writable: true,
});

const listeners = [];
globalThis.window = {
  location: { href: "https://docs.authifi.io/guides/sso-integration-guide/" },
  addEventListener(name) { listeners.push(name); },
};
globalThis.document = document;
globalThis.Event = class Event { constructor(type) { this.type = type; } };

vm.runInThisContext(fs.readFileSync(scenario.source, "utf8"));

(async () => {
  const results = {};
  for (const tool of registered) {
    try {
      results[tool.name] = await tool.execute(scenario.args || {});
    } catch (error) {
      results[tool.name] = { threw: String(error) };
    }
  }
  console.log(JSON.stringify({
    names: registered.map((tool) => tool.name),
    descriptions: Object.fromEntries(registered.map((t) => [t.name, t.description])),
    results,
    listeners,
  }));
})();
"""


def run_webmcp(*, has_search: bool, has_nav: bool, args: dict | None = None) -> dict:
    scenario = {
        "source": str(WEBMCP_SOURCE),
        "hasSearch": has_search,
        "hasNav": has_nav,
        "args": args or {},
    }
    result = subprocess.run(
        ["node", "-e", DRIVER],
        cwd=REPO_ROOT,
        env={**os.environ, "WEBMCP_SCENARIO": json.dumps(scenario)},
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


# --- The obsolete markdown tool must be gone ----------------------------------


def test_the_markdown_url_tool_is_not_registered_on_any_page() -> None:
    """Starlette never negotiates `Accept: text/markdown`, so the tool lied.

    It told agents to re-fetch the page asking for Markdown; they got the same
    HTML back and had no way to tell the difference from a real answer.
    """
    for has_search, has_nav in ((True, True), (False, False)):
        registered = run_webmcp(has_search=has_search, has_nav=has_nav)["names"]
        assert "get_page_markdown_url" not in registered


@pytest.mark.parametrize(
    "obsolete",
    ["get_page_markdown_url", "acceptHeader", "text/markdown", "Markdown for Agents"],
)
def test_the_source_carries_no_markdown_negotiation_instructions(obsolete: str) -> None:
    """Including the instruction strings, which an agent would otherwise read."""
    assert obsolete not in WEBMCP_SOURCE.read_text(encoding="utf-8")


# --- Registration has to match what the page can actually do ------------------


def test_a_protected_page_registers_the_search_and_section_tools() -> None:
    outcome = run_webmcp(has_search=True, has_nav=True, args={"query": "sso"})

    assert sorted(outcome["names"]) == ["list_sections", "search_docs"]
    assert outcome["results"]["search_docs"]["query"] == "sso"
    assert outcome["results"]["list_sections"]["sections"] == [
        {"title": "Guides", "href": "/guides/"},
        {"title": "Security", "href": "/security/"},
    ]


def test_a_public_page_advertises_no_search_tool() -> None:
    """`overrides/main.html` removes the search control from public pages.

    Registering `search_docs` there would hand an agent a tool whose only
    possible answer is "Search input not found on this page".
    """
    outcome = run_webmcp(has_search=False, has_nav=False, args={"query": "privacy"})

    assert outcome["names"] == []


def test_search_is_registered_only_where_the_control_exists() -> None:
    assert "search_docs" in run_webmcp(has_search=True, has_nav=False)["names"]
    assert "search_docs" not in run_webmcp(has_search=False, has_nav=True)["names"]


def test_sections_are_registered_only_where_navigation_exists() -> None:
    assert "list_sections" in run_webmcp(has_search=False, has_nav=True)["names"]
    assert "list_sections" not in run_webmcp(has_search=True, has_nav=False)["names"]


def test_a_registered_page_still_aborts_its_tools_on_navigation() -> None:
    assert "pagehide" in run_webmcp(has_search=True, has_nav=True)["listeners"]
