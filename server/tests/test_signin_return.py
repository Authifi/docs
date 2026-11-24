"""The Sign in link has to carry the open section into `next`.

The header href is the page path. A browser drops the fragment before the
request, so this script copies a heading id into the query string, where
`normalize_next_path` can keep it. These load the script in Node against a
fake anchor, the same way `test_webmcp` loads its script.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SIGNIN_SOURCE = REPO_ROOT / "docs" / "javascripts" / "signin-return.js"
PAGE_HREF = "/_auth/login?next=/guides%2Ftwo-factor-authentication%2F"
PAGE_NEXT = "/guides/two-factor-authentication/"

requires_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not available")

DRIVER = """
const fs = require("fs");
const vm = require("vm");

const scenario = JSON.parse(process.env.SIGNIN_SCENARIO);

const attrs = { href: scenario.href };
const listeners = {};
const link = {
  getAttribute(name) {
    return Object.prototype.hasOwnProperty.call(attrs, name) ? attrs[name] : null;
  },
  setAttribute(name, value) {
    attrs[name] = String(value);
  },
  addEventListener(type, fn) {
    listeners[type] = fn;
  },
};

const windowListeners = {};
const location = { hash: scenario.hash || "", origin: "https://docs.authifi.io" };
globalThis.window = {
  location,
  addEventListener(type, fn) {
    windowListeners[type] = fn;
  },
};
globalThis.document = {
  querySelector(selector) {
    if (!scenario.hasLink || selector !== "a.md-header__signin") {
      return null;
    }
    return link;
  },
};

vm.runInThisContext(fs.readFileSync(scenario.source, "utf8"));

function snapshot() {
  let next = null;
  try {
    next = new URL(attrs.href, location.origin).searchParams.get("next");
  } catch (error) {
    next = null;
  }
  return { href: attrs.href, next };
}

const snapshots = [snapshot()];
for (const step of scenario.steps || []) {
  if (Object.prototype.hasOwnProperty.call(step, "hash")) {
    location.hash = step.hash;
  }
  const bucket = step.event === "click" ? listeners : windowListeners;
  bucket[step.event]();
  snapshots.push(snapshot());
}

process.stdout.write(JSON.stringify({
  snapshots,
  baseNext: attrs["data-authifi-next"] || null,
  listeners: Object.keys(listeners),
  windowListeners: Object.keys(windowListeners),
}));
"""


def run_signin(
    *,
    has_link: bool = True,
    hash: str = "",
    steps: list[dict] | None = None,
) -> dict:
    scenario = {
        "source": str(SIGNIN_SOURCE),
        "href": PAGE_HREF,
        "hasLink": has_link,
        "hash": hash,
        "steps": steps or [],
    }
    result = subprocess.run(
        ["node", "-e", DRIVER],
        cwd=REPO_ROOT,
        env={**os.environ, "SIGNIN_SCENARIO": json.dumps(scenario)},
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


@requires_node
def test_a_page_without_the_sign_in_link_is_left_alone() -> None:
    outcome = run_signin(has_link=False, hash="#set-up-2fa")

    assert outcome["baseNext"] is None
    assert outcome["listeners"] == []
    assert outcome["snapshots"] == [{"href": PAGE_HREF, "next": PAGE_NEXT}]


@requires_node
def test_the_open_section_is_written_into_next_percent_encoded() -> None:
    outcome = run_signin(hash="#set-up-2fa")

    assert outcome["baseNext"] == PAGE_NEXT
    assert outcome["listeners"] == ["click"]
    assert outcome["windowListeners"] == ["hashchange"]
    snapshot = outcome["snapshots"][0]
    assert snapshot["next"] == f"{PAGE_NEXT}#set-up-2fa"
    assert "%23set-up-2fa" in snapshot["href"]
    assert "#" not in snapshot["href"]


@requires_node
def test_a_heading_id_may_start_with_a_digit() -> None:
    outcome = run_signin(hash="#1-install-an-authenticator-app")

    assert outcome["snapshots"][0]["next"] == f"{PAGE_NEXT}#1-install-an-authenticator-app"


@requires_node
@pytest.mark.parametrize("hash", ["", "#<script>", "#set-up-2fa#extra", "#set-up-2fa\n"])
def test_a_hash_that_is_not_a_heading_id_leaves_the_link_unchanged(hash: str) -> None:
    outcome = run_signin(hash=hash)

    assert outcome["snapshots"] == [{"href": PAGE_HREF, "next": PAGE_NEXT}]


@requires_node
def test_a_later_section_replaces_the_fragment_instead_of_stacking_one() -> None:
    outcome = run_signin(
        hash="#set-up-2fa",
        steps=[{"hash": "#sign-in-with-2fa", "event": "hashchange"}],
    )

    assert outcome["snapshots"][0]["next"] == f"{PAGE_NEXT}#set-up-2fa"
    assert outcome["snapshots"][1]["next"] == f"{PAGE_NEXT}#sign-in-with-2fa"
    assert "set-up-2fa" not in outcome["snapshots"][1]["href"]


@requires_node
def test_click_reads_the_hash_even_when_it_changed_without_hashchange() -> None:
    outcome = run_signin(steps=[{"hash": "#save-your-recovery-codes", "event": "click"}])

    assert outcome["snapshots"][0] == {"href": PAGE_HREF, "next": PAGE_NEXT}
    assert outcome["snapshots"][1]["next"] == f"{PAGE_NEXT}#save-your-recovery-codes"


@requires_node
def test_clearing_the_hash_restores_the_original_href() -> None:
    outcome = run_signin(hash="#set-up-2fa", steps=[{"hash": "", "event": "hashchange"}])

    assert outcome["snapshots"][1] == {"href": PAGE_HREF, "next": PAGE_NEXT}
