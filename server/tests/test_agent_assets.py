from __future__ import annotations

import importlib.util
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "docs" / "hooks" / "agent_assets.py"
SPEC = importlib.util.spec_from_file_location("agent_assets", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
agent_assets = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent_assets)


def test_write_sitemap_includes_only_public_content(tmp_path: Path) -> None:
    nav = [
        {"Home": "index.md"},
        {"Guides": ["guides/sso-integration-guide.md", "guides/nhe-delegated-tokens.md"]},
        {"Security": ["security/README.md"]},
    ]

    agent_assets._write_sitemap(tmp_path, "https://docs.authifi.io", nav)

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
        nav=["privacy-policy.md", "terms-of-service.md", "sms-opt-in.html"],
    )

    agent_assets._resolved_nav = None

    agent_assets.on_post_build(config)

    assert (site_dir / "sitemap.xml").exists()
    assert not (site_dir / "_headers").exists()
