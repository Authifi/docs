from __future__ import annotations

from pathlib import Path
import re

import pytest
import yaml

RAW_WORKFLOW = (Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml").read_text(
    encoding="utf-8"
)
WORKFLOW = yaml.safe_load(RAW_WORKFLOW)
VALIDATE_JOB = WORKFLOW["jobs"]["validate"]
STEPS = VALIDATE_JOB["steps"]


def parse_steps(text: str) -> list[dict[str, object]]:
    return yaml.safe_load(text)["jobs"]["validate"]["steps"]


def step(name: str) -> dict[str, object]:
    return next(candidate for candidate in STEPS if candidate.get("name") == name)


def step_run(name: str) -> str:
    return str(step(name).get("run", ""))


def step_shell(name: str) -> str | None:
    value = step(name).get("shell")
    return None if value is None else str(value)


def executable_lines(run: str) -> list[str]:
    lines: list[str] = []
    pending = ""

    for raw_line in run.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        pending = f"{pending} {stripped}".strip() if pending else stripped
        if pending.endswith("\\"):
            pending = pending[:-1].rstrip()
            continue

        lines.append(pending)
        pending = ""

    if pending:
        lines.append(pending)
    return lines


def anchored_lines(lines: list[str], pattern: str) -> list[str]:
    regex = re.compile(pattern)
    return [line for line in lines if regex.match(line)]


def anchored_line(run: str, pattern: str) -> str:
    matches = anchored_lines(executable_lines(run), pattern)
    assert len(matches) == 1, matches
    return matches[0]


def with_line_replaced(run: str, original: str, replacement: str) -> str:
    assert original in run, original
    return run.replace(original, replacement, 1)


def test_ci_job_keeps_named_native_release_and_mock_steps() -> None:
    assert WORKFLOW["permissions"] == {"contents": "read"}
    assert VALIDATE_JOB["runs-on"] == "ubuntu-latest"

    assert step("Build native release artifact")
    assert step("Verify offline release installation")
    assert step("Probe native release server")
    assert step("Credential-free local mock OIDC smoke test")


def test_release_build_step_targets_the_release_archive() -> None:
    run = step_run("Build native release artifact")

    assert 'set -euo pipefail' in executable_lines(run)
    assert (
        anchored_line(run, r'^\.\/scripts\/build-release\.sh "\$GITHUB_SHA" dist/releases$')
        == './scripts/build-release.sh "$GITHUB_SHA" dist/releases'
    )


def test_offline_install_step_extracts_safely_and_installs_from_the_bundled_lock() -> None:
    run = step_run("Verify offline release installation")
    lines = executable_lines(run)

    assert step_shell("Verify offline release installation") == "bash"
    assert "set -euo pipefail" in lines
    assert 'python - "dist/releases/$GITHUB_SHA.tar.gz" "dist/expanded" <<\'PY\'' in lines
    assert 'archive = Path(sys.argv[1])' in run
    assert 'destination = Path(sys.argv[2])' in run
    assert 'with tarfile.open(archive) as release:' in run
    assert 'release.extractall(destination, filter="data")' in run
    assert 'cmp --silent dist/expanded/requirements.txt server/requirements.txt' in lines
    assert (
        anchored_line(
            run,
            r"^python -m pip install --no-index --find-links=dist/expanded/wheelhouse -r dist/expanded/requirements\.txt$",
        )
        == "python -m pip install --no-index --find-links=dist/expanded/wheelhouse -r dist/expanded/requirements.txt"
    )


def test_native_probe_step_runs_the_extracted_release_and_checks_bypass_bodies() -> None:
    run = step_run("Probe native release server")
    lines = executable_lines(run)

    assert step_shell("Probe native release server") == "bash"
    assert "set -euo pipefail" in lines
    assert "trap cleanup_server EXIT" in lines
    assert 'kill "$pid" 2>/dev/null || true' in run
    assert 'wait "$pid" 2>/dev/null || true' in run
    assert 'cat dist/uvicorn.log >&2' in run
    assert 'export SITE_DIR="$PWD/dist/expanded/site"' in lines
    uvicorn_line = next(
        line for line in lines if line.startswith("dist/runtime/bin/uvicorn server.main:app ")
    )
    assert "--app-dir dist/expanded" in uvicorn_line
    assert any(line == 'for _ in $(seq 1 30); do' for line in lines)
    assert any("/health" in line for line in lines)
    assert any("--path-as-is" in line for line in lines)
    assert 'body_file="$(mktemp)"' in lines
    assert '--output "$body_file"' in run
    assert "from server.local_smoke import BYPASS_PROBE_PATHS" in run
    assert "from server.local_smoke import PROTECTED_CONTENT_MARKERS" in run
    assert 'rm -f "$body_file"' in run


def test_ci_keeps_mock_oidc_smoke_explicit_and_avoids_production_docker_builds() -> None:
    mock_run = step_run("Credential-free local mock OIDC smoke test")
    shell_text = "\n".join(step_run(candidate["name"]) for candidate in STEPS).lower()

    assert "python -m server.local_smoke" in executable_lines(mock_run)
    assert "docker build --tag authifi-docs:test ." not in shell_text
    with pytest.raises(StopIteration):
        step("Build container image")


def test_structural_parsing_ignores_comments_and_dead_strings() -> None:
    poisoned = (
        RAW_WORKFLOW
        + "\n# - name: Build native release artifact\n"
        + "#   run: docker build --tag authifi-docs:test .\n"
        + 'dead_text: "docker build --tag authifi-docs:test . dist/releases --no-index"\n'
    )

    parsed = parse_steps(poisoned)

    assert [candidate["name"] for candidate in parsed] == [candidate["name"] for candidate in STEPS]
    assert next(
        candidate for candidate in parsed if candidate["name"] == "Credential-free local mock OIDC smoke test"
    )["run"] == step("Credential-free local mock OIDC smoke test")["run"]


def test_bundled_lock_checks_reject_commented_and_inert_mentions() -> None:
    install_run = step_run("Verify offline release installation")
    probe_run = step_run("Probe native release server")

    commented_cmp = with_line_replaced(
        install_run,
        "cmp --silent dist/expanded/requirements.txt server/requirements.txt",
        "# cmp --silent dist/expanded/requirements.txt server/requirements.txt",
    )
    inert_body = with_line_replaced(
        probe_run,
        'body_file="$(mktemp)"',
        ': "body_file=$(mktemp) --output $body_file from server.local_smoke import PROTECTED_CONTENT_MARKERS"',
    )

    with pytest.raises(AssertionError):
        assert 'cmp --silent dist/expanded/requirements.txt server/requirements.txt' in executable_lines(
            commented_cmp
        )
    with pytest.raises(AssertionError):
        assert 'body_file="$(mktemp)"' in executable_lines(inert_body)
