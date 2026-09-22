"""The scheduled lock refresh rewrites versions and refuses a changed package set."""

from __future__ import annotations

import importlib.util
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
