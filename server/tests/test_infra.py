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
import shutil
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


# --- The backend verification snippet has to actually verify ------------------
#
# A bare `grep '"type"' ...` prints nothing and exits 0 when the file is
# missing, so the one command a caller runs to confirm their state is going to
# S3 was silent in exactly the case that matters.

BACKEND_CHECK_FUNCTION = "check_terraform_backend"

requires_jq = pytest.mark.skipif(shutil.which("jq") is None, reason="jq is not available")


def backend_check_snippet() -> str:
    blocks = re.findall(r"```bash\n(.*?)```", readme_text(), flags=re.DOTALL)
    matching = [block for block in blocks if BACKEND_CHECK_FUNCTION in block]

    assert len(matching) == 1, (
        f"expected exactly one README block defining {BACKEND_CHECK_FUNCTION}, found {len(matching)}"
    )
    return matching[0]


def run_backend_check(tmp_path: Path, state: str | None) -> subprocess.CompletedProcess[str]:
    """Run the README's snippet verbatim against a fabricated init result."""
    if state is not None:
        state_file = tmp_path / "infra" / ".terraform" / "terraform.tfstate"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(state, encoding="utf-8")

    script = tmp_path / "check.sh"
    script.write_text(backend_check_snippet(), encoding="utf-8")
    return subprocess.run(
        ["bash", str(script)], cwd=tmp_path, capture_output=True, text=True
    )


@requires_jq
def test_the_backend_check_passes_on_a_real_s3_init(tmp_path: Path) -> None:
    result = run_backend_check(tmp_path, '{"version": 3, "backend": {"type": "s3"}}')

    assert result.returncode == 0, result.stderr
    assert "s3" in result.stdout


@requires_jq
def test_the_backend_check_fails_loudly_when_init_never_ran(tmp_path: Path) -> None:
    result = run_backend_check(tmp_path, state=None)

    assert result.returncode != 0
    assert result.stderr.strip()


@requires_jq
@pytest.mark.parametrize("state", ['{"backend": {"type": "local"}}', "{}", '{"backend": {}}'])
def test_the_backend_check_fails_loudly_on_any_other_backend(
    tmp_path: Path, state: str
) -> None:
    """Including the silent local fallback this whole section exists to catch."""
    result = run_backend_check(tmp_path, state)

    assert result.returncode != 0
    assert result.stderr.strip()


@requires_jq
def test_the_backend_check_names_the_backend_it_actually_found(tmp_path: Path) -> None:
    result = run_backend_check(tmp_path, '{"backend": {"type": "local"}}')

    assert "local" in result.stderr


def test_the_backend_check_does_not_rely_on_a_bare_grep() -> None:
    snippet = backend_check_snippet()

    assert "return 1" in snippet or "exit 1" in snippet
    assert not re.search(r"^\s*grep\s", snippet, flags=re.MULTILINE)
