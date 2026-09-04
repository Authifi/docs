"""Guards on the Terraform root's state-backend story.

The root deliberately declares no backend so each caller picks their own state
storage. That choice has a sharp edge: `terraform init -backend-config=...`
against a config with no `backend` block does not fail, it emits a warning and
silently initialises the *local* backend. Documenting the bare `-backend-config`
form therefore reads like an S3 setup while writing state to disk, so the
README has to tell callers to declare the backend first.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INFRA_DIR = REPO_ROOT / "infra"
INFRA_README = INFRA_DIR / "README.md"

# The file callers create locally to opt into a remote backend. Terraform loads
# `*_override.tf` last, so it can introduce the block the committed config omits.
BACKEND_OVERRIDE_FILENAME = "backend_override.tf"

BACKEND_BLOCK_PATTERN = re.compile(r"^\s*backend\s+\"[a-z0-9]+\"\s*\{", re.MULTILINE)


def readme_text() -> str:
    return INFRA_README.read_text(encoding="utf-8")


def test_no_committed_terraform_file_declares_a_backend() -> None:
    """Committing one would take the choice away and bake in account specifics."""
    for path in sorted(INFRA_DIR.glob("*.tf")):
        assert not BACKEND_BLOCK_PATTERN.search(path.read_text(encoding="utf-8")), (
            f"{path.name} declares a backend; the root must leave that to the caller"
        )


def test_the_backend_override_file_is_git_ignored() -> None:
    """It holds a caller's own backend choice and must never be committed."""
    result = subprocess.run(
        ["git", "check-ignore", "--", f"infra/{BACKEND_OVERRIDE_FILENAME}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"infra/{BACKEND_OVERRIDE_FILENAME} is not git-ignored: {result.stderr or result.stdout}"
    )


def test_the_backend_override_file_is_not_committed() -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "--", f"infra/{BACKEND_OVERRIDE_FILENAME}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    assert not tracked.stdout.strip()


def test_readme_tells_s3_callers_to_declare_the_backend_first() -> None:
    text = readme_text()

    assert BACKEND_OVERRIDE_FILENAME in text
    assert 'backend "s3" {}' in text


def test_readme_never_shows_a_partial_config_init_before_the_declaration() -> None:
    """Order matters: a runnable `-backend-config` init on its own is the bug.

    Prose may name the flag earlier while explaining the trap; what must not
    come first is a command a reader could copy and run.
    """
    text = readme_text()

    invocations = [
        match.start()
        for match in re.finditer(r"^\s*-backend-config=", text, re.MULTILINE)
    ]
    assert invocations, "README no longer shows a partial-config init"

    declaration = text.find('backend "s3" {}')
    assert declaration != -1
    assert declaration < min(invocations), (
        f"README shows a runnable -backend-config init before the "
        f"{BACKEND_OVERRIDE_FILENAME} declaration"
    )


def test_readme_still_documents_the_no_backend_local_default() -> None:
    """Callers who want local state must not be pushed into creating a file."""
    text = readme_text()

    assert "terraform -chdir=infra init" in text
    assert "local" in text.lower()
