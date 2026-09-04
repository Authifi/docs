"""What `/health` is allowed to claim.

The probe decides whether App Runner routes traffic to a deployment and whether
Compose calls the container healthy, so answering `200` because the process is
running is worse than useless: a runtime stage that shipped without its built
site answers every request with a `404`, and a green health check would make
that the live deployment.

So the check reads the site instead of the process: the front page, and the page
every logout lands on -- which is also the compliance document that has to stay
publicly reachable. A failure names neither the path nor the directory, because
this endpoint is served to anyone.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from server.app import (
    HEALTH_REQUIRED_ARTIFACTS,
    DEFAULT_POST_LOGOUT_PATH,
    site_relative_path,
    unhealthy_site_artifacts,
)
from server.tests.support import build_client, build_config

REQUIRED_RELATIVE_PATHS = ("index.html", "privacy-policy/index.html")


def test_a_complete_site_is_healthy(site_dir: Path) -> None:
    client = build_client(site_dir)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_the_checked_artifacts_are_the_front_page_and_the_logout_landing_page() -> None:
    """Stated as a list so it is reviewable, and tied to the configured target."""
    assert HEALTH_REQUIRED_ARTIFACTS == REQUIRED_RELATIVE_PATHS
    assert site_relative_path(DEFAULT_POST_LOGOUT_PATH) in HEALTH_REQUIRED_ARTIFACTS


@pytest.mark.parametrize("missing", REQUIRED_RELATIVE_PATHS)
def test_a_missing_required_artifact_is_unhealthy(site_dir: Path, missing: str) -> None:
    (site_dir / missing).unlink()
    client = build_client(site_dir)

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


def test_a_site_directory_that_does_not_exist_at_all_is_unhealthy(tmp_path: Path) -> None:
    """The misconfiguration that motivated this: SITE_DIR pointing nowhere."""
    client = build_client(tmp_path / "nothing-here")

    response = client.get("/health")

    assert response.status_code == 503


@pytest.mark.parametrize("required", REQUIRED_RELATIVE_PATHS)
def test_an_artifact_that_is_a_directory_is_unhealthy(site_dir: Path, required: str) -> None:
    """Portable stand-in for unreadable: present, named right, not a file."""
    target = site_dir / required
    target.unlink()
    target.mkdir()
    client = build_client(site_dir)

    assert client.get("/health").status_code == 503


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads regardless of mode bits")
def test_an_unreadable_artifact_is_unhealthy(site_dir: Path) -> None:
    """Present, a file, the right size, and the process cannot open it.

    This is the case a `Path.is_file()` check answers "healthy" to, which is why
    the check opens the file rather than asking about it.
    """
    unreadable = site_dir / "index.html"
    unreadable.chmod(0o000)
    try:
        client = build_client(site_dir)

        assert client.get("/health").status_code == 503
    finally:
        unreadable.chmod(0o644)


def test_an_empty_artifact_is_unhealthy(site_dir: Path) -> None:
    """A zero-byte index.html is a failed build, not a front page."""
    (site_dir / "index.html").write_text("", encoding="utf-8")
    client = build_client(site_dir)

    assert client.get("/health").status_code == 503


def test_the_failure_names_nothing_about_the_filesystem(site_dir: Path) -> None:
    """Anyone can reach `/health`, including before they sign in."""
    (site_dir / "index.html").unlink()
    client = build_client(site_dir)

    response = client.get("/health")
    body = response.text

    assert json.loads(body) == {"status": "unavailable"}
    assert str(site_dir) not in body
    assert "index.html" not in body
    assert "privacy-policy" not in body
    assert "/app" not in body


def test_the_unhealthy_answer_is_not_cached(site_dir: Path) -> None:
    """A cached 503 would outlive the deployment that fixed it."""
    (site_dir / "index.html").unlink()
    client = build_client(site_dir)

    response = client.get("/health")

    assert response.headers["cache-control"] == "private, no-store"


def test_the_healthy_answer_is_not_cached_either(site_dir: Path) -> None:
    client = build_client(site_dir)

    assert client.get("/health").headers["cache-control"] == "private, no-store"


def test_the_probe_reports_which_artifacts_failed_for_the_log(site_dir: Path) -> None:
    """Operators need the detail the response withholds."""
    (site_dir / "privacy-policy" / "index.html").unlink()

    assert unhealthy_site_artifacts(build_config(site_dir)) == ["privacy-policy/index.html"]


def test_the_probe_reports_nothing_for_a_complete_site(site_dir: Path) -> None:
    assert unhealthy_site_artifacts(build_config(site_dir)) == []


def test_a_non_default_logout_target_is_the_one_checked(site_dir: Path) -> None:
    """The landing page is only required because logout sends users there."""
    (site_dir / "terms-of-service" / "index.html").unlink()
    client = build_client(site_dir, post_logout_path="/terms-of-service/")

    assert client.get("/health").status_code == 503

    still_present = build_client(site_dir, post_logout_path="/privacy-policy/")
    assert still_present.get("/health").status_code == 200
