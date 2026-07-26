import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SITE_URL = "https://docs.authifi.io"
SITEMAP_NAMESPACE = "http://www.sitemaps.org/schemas/sitemap/0.9"
HOOK_SPEC = importlib.util.spec_from_file_location(
    "agent_assets", REPOSITORY_ROOT / "docs" / "hooks" / "agent_assets.py"
)
if HOOK_SPEC is None or HOOK_SPEC.loader is None:
    raise RuntimeError("Could not load docs/hooks/agent_assets.py")
agent_assets = importlib.util.module_from_spec(HOOK_SPEC)
HOOK_SPEC.loader.exec_module(agent_assets)


class GeneratedPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag == "title":
            self._in_title = True
        if tag == "a":
            href = dict(attrs).get("href")
            if href is not None:
                self.links.append(href)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data


class SitemapGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary_directory.cleanup)
        self.site_dir = Path(self._temporary_directory.name)

    def _locations(self, nav: object) -> list[str]:
        agent_assets._write_sitemap(self.site_dir, SITE_URL, nav)
        namespace = f"{{{SITEMAP_NAMESPACE}}}"
        sitemap_root = ET.parse(self.site_dir / "sitemap.xml").getroot()
        return [
            location.text or ""
            for location in sitemap_root.findall(f"{namespace}url/{namespace}loc")
        ]

    def test_raw_dictionary_navigation_is_supported(self) -> None:
        locations = self._locations(
            {
                "Home": "index.md",
                "Security": {"Overview": "security/README.md"},
            }
        )

        self.assertEqual(locations, [f"{SITE_URL}/", f"{SITE_URL}/security/"])

    def test_external_navigation_links_are_excluded(self) -> None:
        locations = self._locations(
            [
                "guides/tenant-admin-guide.md",
                "https://example.com/docs/",
                f"{SITE_URL}/security/",
            ]
        )

        self.assertEqual(
            locations,
            [
                f"{SITE_URL}/guides/tenant-admin-guide/",
                f"{SITE_URL}/security/",
            ],
        )


class DocsBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary_directory = tempfile.TemporaryDirectory()
        cls.site_dir = Path(cls._temporary_directory.name) / "site"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "mkdocs",
                "build",
                "--strict",
                "--site-dir",
                str(cls.site_dir),
            ],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError(
                f"MkDocs build failed:\n{result.stdout}\n{result.stderr}"
            )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary_directory.cleanup()

    def test_generated_html_contains_navigation(self) -> None:
        home_page = self.site_dir / "index.html"
        security_page = self.site_dir / "security" / "index.html"
        self.assertTrue(home_page.is_file())
        self.assertTrue(security_page.is_file())

        parser = GeneratedPageParser()
        parser.feed(home_page.read_text(encoding="utf-8"))

        self.assertIn("Authifi Documentation", parser.title)
        self.assertTrue(
            any(link.rstrip("/").endswith("security") for link in parser.links)
        )

    def test_search_index_contains_generated_pages(self) -> None:
        search_index = json.loads(
            (self.site_dir / "search" / "search_index.json").read_text(
                encoding="utf-8"
            )
        )
        documents = search_index["docs"]

        self.assertIsInstance(documents, list)
        self.assertGreater(len(documents), 0)
        self.assertTrue(
            any(
                document.get("location", "").split("#", 1)[0] == "security/"
                for document in documents
            )
        )

    def test_sync_manifest_declares_synced_files(self) -> None:
        manifest = json.loads(
            (REPOSITORY_ROOT / "docs" / ".authifi-sync.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(
            manifest["$generatedBy"], "auth-monorepo scripts/sync-authifi-docs"
        )
        self.assertIsInstance(manifest["note"], str)
        self.assertGreater(len(manifest["files"]), 0)
        for synced_file in manifest["files"]:
            self.assertIsInstance(synced_file["source"], str)
            self.assertIsInstance(synced_file["dest"], str)

    def test_agent_discovery_assets_are_generated(self) -> None:
        expected_assets = (
            "robots.txt",
            "_headers",
            "auth.md",
            ".well-known/api-catalog",
            ".well-known/agent-skills/index.json",
        )
        for relative_path in expected_assets:
            self.assertTrue(
                (self.site_dir / relative_path).is_file(), relative_path
            )

        headers = (self.site_dir / "_headers").read_text(encoding="utf-8")
        self.assertIn("Content-Type: application/linkset+json", headers)
        self.assertIn("Content-Type: application/json", headers)
        self.assertIn("Content-Type: text/markdown; charset=utf-8", headers)

        api_catalog = json.loads(
            (self.site_dir / ".well-known" / "api-catalog").read_text(
                encoding="utf-8"
            )
        )
        self.assertIsInstance(api_catalog["linkset"], list)

        skill_index = json.loads(
            (
                self.site_dir / ".well-known" / "agent-skills" / "index.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            skill_index["$schema"],
            "https://schemas.agentskills.io/discovery/0.2.0/schema.json",
        )
        for skill in skill_index["skills"]:
            self.assertEqual(skill["type"], "skill-md")
            self.assertTrue(skill["digest"].startswith("sha256:"))
            self.assertTrue(skill["url"].startswith(f"{SITE_URL}/"))
            relative_path = skill["url"].removeprefix(f"{SITE_URL}/")
            self.assertTrue((self.site_dir / relative_path).is_file(), relative_path)

    def test_sitemap_locations_have_built_artifacts(self) -> None:
        sitemap_root = ET.parse(self.site_dir / "sitemap.xml").getroot()
        namespace = f"{{{SITEMAP_NAMESPACE}}}"
        self.assertEqual(sitemap_root.tag, f"{namespace}urlset")

        locations = [
            location.text
            for location in sitemap_root.findall(f"{namespace}url/{namespace}loc")
        ]
        self.assertGreater(len(locations), 0)
        self.assertEqual(len(locations), len(set(locations)))

        for location in locations:
            self.assertIsNotNone(location)
            self.assertTrue(location.startswith(SITE_URL), location)
            relative_url = location.removeprefix(SITE_URL)
            if relative_url in ("", "/"):
                relative_artifact = Path("index.html")
            elif relative_url.endswith("/"):
                relative_artifact = Path(relative_url.lstrip("/")) / "index.html"
            else:
                relative_artifact = Path(relative_url.lstrip("/"))

            self.assertTrue(
                (self.site_dir / relative_artifact).is_file(),
                f"Sitemap location {location} maps to missing built artifact "
                f"{relative_artifact}",
            )


if __name__ == "__main__":
    unittest.main()
