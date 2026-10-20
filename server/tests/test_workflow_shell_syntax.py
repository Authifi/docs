"""Every `run:` block in every workflow has to parse as the shell it declares.

The failure this file exists for is invisible to `yaml.safe_load` and to every
other test in this directory: a quoted heredoc (`<<'PY'`) whose terminator is
indented never terminates, so the shell swallows the rest of the step and dies
with `unexpected end of file`. YAML block scalars strip only the *block's* own
indentation, so a heredoc nested inside an `if` or a `for` keeps whatever
indentation it was written with, and the step is broken from the moment it is
committed. Nothing catches it until a runner reaches that step, minutes into a
job, after everything ahead of it has already run.

The blocks are read structurally out of the parsed YAML rather than grepped, so
the shell that gets checked is the one the step declares, and a `run:` added
tomorrow is covered without anyone remembering to list it here.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, Mapping, NamedTuple

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = ROOT / ".github" / "workflows"

# GitHub runs a `run:` block through `bash -e {0}` on Linux runners unless the
# step, the job, or the workflow names another shell.
DEFAULT_SHELL = "bash"

# How to ask an interpreter to parse a script without running it. A shell that
# is not listed is a shell this file cannot make an honest claim about, so the
# enumeration below fails rather than skipping it.
SYNTAX_CHECKERS: Mapping[str, list[str]] = {
    "bash": ["bash", "-n"],
    "sh": ["sh", "-n"],
}

# The exact shape of the bug, kept next to the checker so the assertion is
# exercised against something it must reject. Dedented by the YAML loader to
# two-space indentation, the terminator no longer sits at the start of a line.
INDENTED_HEREDOC = """
for probe in a b; do
  if ! python - <<'PY'
  print("hi")
  PY
  then
    exit 1
  fi
done
"""


class RunBlock(NamedTuple):
    workflow: str
    job: str
    step: str
    shell: str
    script: str

    @property
    def label(self) -> str:
        return f"{self.workflow}::{self.job}::{self.step}"


def workflow_files() -> list[Path]:
    return sorted(
        path
        for pattern in ("*.yml", "*.yaml")
        for path in WORKFLOW_DIR.glob(pattern)
    )


def _declared_default_shell(*scopes: Mapping[str, Any] | None) -> str | None:
    """The nearest `defaults.run.shell`, job scope before workflow scope."""
    for scope in scopes:
        shell = ((scope or {}).get("defaults") or {}).get("run", {}).get("shell")
        if shell:
            return str(shell)
    return None


def collect_run_blocks() -> list[RunBlock]:
    blocks: list[RunBlock] = []

    for path in workflow_files():
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_name, job in (document.get("jobs") or {}).items():
            for index, step in enumerate(job.get("steps") or []):
                script = step.get("run")
                if script is None:
                    continue
                shell = (
                    step.get("shell")
                    or _declared_default_shell(job, document)
                    or DEFAULT_SHELL
                )
                blocks.append(
                    RunBlock(
                        workflow=path.name,
                        job=str(job_name),
                        step=str(step.get("name") or f"step {index}"),
                        shell=str(shell),
                        script=str(script),
                    )
                )

    return blocks


RUN_BLOCKS = collect_run_blocks()


def parse_script(shell: str, script: str) -> subprocess.CompletedProcess[str]:
    checker = SYNTAX_CHECKERS[shell]
    return subprocess.run(checker, input=script, capture_output=True, text=True, check=False)


def test_the_collector_finds_the_workflows_and_their_run_blocks() -> None:
    """A parser that silently found nothing would make every check below pass."""
    files = {path.name for path in workflow_files()}
    workflows = {block.workflow for block in RUN_BLOCKS}

    assert {"ci.yml", "deploy.yml"} <= files

    # Cross-checked against the raw text: every workflow that declares a `run:`
    # step at all has to have contributed at least one block to the enumeration.
    assert workflows == {
        name
        for name in files
        if re.search(r"(?m)^\s*run:\s*\S", (WORKFLOW_DIR / name).read_text(encoding="utf-8"))
    }
    assert len(RUN_BLOCKS) >= 20


def test_every_run_block_declares_a_shell_this_file_can_parse() -> None:
    """A `python` or `pwsh` step is not a thing `bash -n` has an opinion about.
    Extend `SYNTAX_CHECKERS` rather than letting it go unchecked."""
    for block in RUN_BLOCKS:
        assert block.shell in SYNTAX_CHECKERS, f"{block.label} declares shell {block.shell!r}"


@pytest.mark.parametrize("block", RUN_BLOCKS, ids=lambda block: block.label)
def test_every_run_block_parses_as_its_declared_shell(block: RunBlock) -> None:
    completed = parse_script(block.shell, block.script)

    assert completed.returncode == 0, (
        f"{block.label} is not valid {block.shell}:\n{completed.stderr}\n"
        f"--- script ---\n{block.script}"
    )


def test_the_parse_check_rejects_an_indented_heredoc_terminator() -> None:
    """Proof that the check above has teeth, on the shape that motivated it."""
    completed = parse_script("bash", INDENTED_HEREDOC)

    assert completed.returncode != 0
    assert "unexpected end of file" in completed.stderr
