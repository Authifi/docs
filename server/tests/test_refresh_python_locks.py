"""The scheduled lock refresh rewrites versions and refuses a changed package set."""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "refresh_python_locks.py"

SAMPLE_LOCK = """\
# Generated file
mkdocs==1.6.1
idna==3.19  # via requests
typing_extensions==4.16.0  # via pydantic
urllib3==2.7.0  # via requests
"""


def refresh_module():
    spec = importlib.util.spec_from_file_location("refresh_python_locks", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_version_only_updates_keep_via_notes_and_lock_spelling() -> None:
    apply_freeze_to_lock = refresh_module().apply_freeze_to_lock
    rewritten = apply_freeze_to_lock(
        SAMPLE_LOCK,
        {
            "mkdocs": "1.6.1",
            "idna": "3.20",
            "typing-extensions": "4.16.0",
            "urllib3": "2.8.0",
        },
    )

    assert "idna==3.20  # via requests" in rewritten
    assert "urllib3==2.8.0  # via requests" in rewritten
    assert "typing_extensions==4.16.0  # via pydantic" in rewritten
    assert "mkdocs==1.6.1" in rewritten
    assert rewritten.splitlines()[0] == "# Generated file"


def test_identical_freeze_is_a_no_op() -> None:
    apply_freeze_to_lock = refresh_module().apply_freeze_to_lock
    freeze = {
        "mkdocs": "1.6.1",
        "idna": "3.19",
        "typing-extensions": "4.16.0",
        "urllib3": "2.7.0",
    }
    assert apply_freeze_to_lock(SAMPLE_LOCK, freeze) == SAMPLE_LOCK


def test_a_new_package_in_the_freeze_is_refused() -> None:
    module = refresh_module()
    with pytest.raises(module.PackageSetChanged, match="platformdirs"):
        module.apply_freeze_to_lock(
            SAMPLE_LOCK,
            {
                "mkdocs": "1.6.1",
                "idna": "3.19",
                "typing-extensions": "4.16.0",
                "urllib3": "2.7.0",
                "platformdirs": "4.11.12",
            },
        )


def test_a_package_missing_from_the_freeze_is_refused() -> None:
    module = refresh_module()
    with pytest.raises(module.PackageSetChanged, match="urllib3"):
        module.apply_freeze_to_lock(
            SAMPLE_LOCK,
            {
                "mkdocs": "1.6.1",
                "idna": "3.19",
                "typing-extensions": "4.16.0",
            },
        )


def test_a_direct_pin_drift_is_refused() -> None:
    module = refresh_module()
    with pytest.raises(module.DirectPinDrift, match="mkdocs"):
        module.apply_freeze_to_lock(
            SAMPLE_LOCK,
            {
                "mkdocs": "1.6.2",
                "idna": "3.19",
                "typing-extensions": "4.16.0",
                "urllib3": "2.7.0",
            },
            protected={"mkdocs"},
        )


def test_overlapping_pins_must_agree() -> None:
    module = refresh_module()
    with pytest.raises(module.LockOverlapConflict, match="urllib3"):
        module.assert_overlapping_versions(
            "idna==3.20  # via requests\nurllib3==2.8.0  # via requests\n",
            "idna==3.20  # via httpx\nurllib3==2.7.0  # via botocore\n",
        )


def test_stale_via_note_is_refused() -> None:
    module = refresh_module()
    with pytest.raises(module.ViaNotesStale, match="idna"):
        module.assert_via_notes(
            SAMPLE_LOCK,
            {"requests": {"certifi"}, "pydantic": {"typing-extensions"}},
            directs={"mkdocs"},
        )


def test_active_environment_markers_are_kept() -> None:
    module = refresh_module()
    needed = module.needed_from_declared(
        [
            'cffi>=1; implementation_name != "pypy"',
            'pycparser; implementation_name != "pypy"',
            'typing_extensions>=4.5; python_version < "3.13"',
            'exceptiongroup>=1.0.2; python_version < "3.11"',
        ],
        module.BUILD_ENVIRONMENT,
    )
    assert needed == {"cffi", "pycparser", "typing-extensions"}


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI is not available")
def test_server_freeze_keeps_marked_cpython_via_edges() -> None:
    module = refresh_module()
    freeze = module.freeze_after_installing(
        ROOT / "server" / "requirements.in",
        module.build_image(),
    )
    assert "cffi" in freeze.requires["cryptography"]
    assert "pycparser" in freeze.requires["cffi"]
    assert "typing-extensions" in freeze.requires["anyio"]
    module.assert_via_notes(
        (ROOT / "server" / "requirements.txt").read_text(encoding="utf-8"),
        freeze.requires,
        directs=set(module.pinned_directs(ROOT / "server" / "requirements.in")),
    )


def test_refresh_locks_writes_nothing_if_the_second_lock_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = refresh_module()
    site_in = tmp_path / "requirements.in"
    site_lock = tmp_path / "requirements.txt"
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    server_in = server_dir / "requirements.in"
    server_lock = server_dir / "requirements.txt"
    original_site = "mkdocs==1.6.1\nidna==3.19  # via requests\n"
    site_in.write_text("mkdocs==1.6.1\n", encoding="utf-8")
    site_lock.write_text(original_site, encoding="utf-8")
    server_in.write_text("httpx==0.28.1\n", encoding="utf-8")
    server_lock.write_text("httpx==0.28.1\nidna==3.19  # via httpx\n", encoding="utf-8")

    def fake_freeze(direct: Path, image: str):
        del image
        if direct == server_in:
            raise module.PackageSetChanged("added: foo")
        return module.Freeze(
            versions={"mkdocs": "1.6.1", "idna": "3.20"},
            requires={"mkdocs": set(), "requests": {"idna"}},
        )

    monkeypatch.setattr(module, "LOCK_PAIRS", ((site_in, site_lock), (server_in, server_lock)))
    monkeypatch.setattr(module, "freeze_after_installing", fake_freeze)
    monkeypatch.setattr(module, "build_image", lambda: "python:3.12-slim")

    with pytest.raises(module.PackageSetChanged, match="foo"):
        module.refresh_locks()
    assert site_lock.read_text(encoding="utf-8") == original_site
