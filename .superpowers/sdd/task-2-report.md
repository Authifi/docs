# Task 2 Report: Self-Contained Release Builder

## Summary

Implemented a self-contained release builder in `scripts/build-release.sh`, added the `release` target to `Makefile`, and covered the artifact with focused release-layout tests in `server/tests/test_release_artifact.py`.

## Files Changed

- Modified: `Makefile`
- Added: `scripts/build-release.sh`
- Added: `server/tests/test_release_artifact.py`

## Required Corrections To Brief Snippets

1. The brief's host `python3.12` assumption was not portable to this macOS machine, which has `.venv/bin/python` on 3.14 but no `python3.12` on `PATH`; the builder therefore prefers `${PYTHON}` then repo `.venv/bin/python`, then `python3`.
2. The brief's `shasum -a 256` is not the most portable checksum path across macOS and Ubuntu, so the builder writes the `.sha256` file with Python instead.
3. The brief's offline install snippet needs `docker run --platform linux/amd64` on Apple Silicon; without it Docker chooses an arm64 Python 3.12 container that cannot install the x86_64 wheels the task explicitly requires.
4. To keep the RED step as a real assertion failure rather than an unhandled fixture process error, the test first asserts that `scripts/build-release.sh` exists before invoking it.

## RED Evidence

Command:

```bash
.venv/bin/python -m pytest server/tests/test_release_artifact.py -v
```

Result:

```text
collected 3 items
server/tests/test_release_artifact.py::test_release_contains_site_server_lock_and_wheelhouse ERROR
server/tests/test_release_artifact.py::test_release_checksum_matches_archive ERROR
server/tests/test_release_artifact.py::test_release_dependencies_install_without_an_index ERROR
AssertionError: missing release builder: /Users/keats.kirsch/Documents/GitHub/authifi-docs-wt/lsa-10037-aws-oidc/scripts/build-release.sh
```

This failed for the intended reason: the release builder did not exist yet.

## Implementation

- `scripts/build-release.sh` now:
  - validates a 40-char lowercase hex SHA argument
  - builds the MkDocs site into a temporary release tree
  - copies `server/app.py`, `server/main.py`, `server/__init__.py`, and `server/requirements.txt` (renamed to archive-root `requirements.txt`)
  - downloads Linux x86_64 Python 3.12 wheels into `wheelhouse/`
  - writes a deterministic `tar.gz` and matching `.sha256`
- `Makefile` now exposes `make release` with `RELEASE_SHA` and `RELEASE_DIR` overrides.

## GREEN Evidence

Command:

```bash
.venv/bin/python -m pytest server/tests/test_release_artifact.py -v
```

Result:

```text
collected 3 items
server/tests/test_release_artifact.py::test_release_contains_site_server_lock_and_wheelhouse PASSED
server/tests/test_release_artifact.py::test_release_checksum_matches_archive PASSED
server/tests/test_release_artifact.py::test_release_dependencies_install_without_an_index PASSED
========================= 3 passed, 1 warning in 5.18s =========================
```

Manual release and offline-install verification:

```bash
tmpdir=$(mktemp -d)
sha=2222222222222222222222222222222222222222
RELEASE_SHA="$sha" RELEASE_DIR="$tmpdir" make release
mkdir -p "$tmpdir/extracted"
.venv/bin/python - <<'PY' "$tmpdir" "$sha"
import sys, tarfile
from pathlib import Path
root = Path(sys.argv[1])
sha = sys.argv[2]
with tarfile.open(root / f"{sha}.tar.gz") as archive:
    archive.extractall(root / "extracted", filter="data")
PY
image=$(.venv/bin/python - <<'PY'
import re
from pathlib import Path
text = Path("Dockerfile").read_text(encoding="utf-8")
print(re.search(r"python:3\.12-slim@sha256:[0-9a-f]{64}", text).group(0))
PY
)
docker run --rm --platform linux/amd64 --volume "$tmpdir/extracted:/release:ro" "$image" sh -c 'python -m pip install --no-cache-dir --no-index --find-links /release/wheelhouse -r /release/requirements.txt'
```

Result:

```text
make release -> archive and checksum created successfully
docker offline install -> Successfully installed anyio authlib certifi cffi click cryptography h11 httpcore httpx idna itsdangerous pycparser starlette typing_extensions uvicorn
```

## Self-Review

- The builder is intentionally narrow and task-scoped: it packages only the release roots named in the brief and does not invent the Task 3 installer.
- The test proves all three required behaviors: archive layout, checksum integrity, and offline Linux x86_64 Python 3.12 installation.
- The deterministic archive path normalizes gzip mtime and tar metadata, which is the smallest portable way to avoid host-specific tar/gzip differences.

## Concerns

- `mkdocs build --strict` emits an upstream Material for MkDocs warning banner unrelated to this change; the build still succeeds.
- The focused pytest run still reports one pre-existing Starlette/AnyIO deprecation warning from the virtualenv test dependencies.
- The release artifact intentionally does not include any installer or deployment wrapper; Task 3 is expected to consume this artifact and add that layer.

## Review Fix Addendum

### Review Item 1: Preserve runtime `docs/_headers`

RED command:

```bash
.venv/bin/python -m pytest server/tests/test_release_artifact.py -v
```

RED result:

```text
collected 4 items
server/tests/test_release_artifact.py::test_release_contains_site_server_lock_and_wheelhouse PASSED
server/tests/test_release_artifact.py::test_release_preserves_root_link_header_behavior FAILED
server/tests/test_release_artifact.py::test_release_checksum_matches_archive PASSED
server/tests/test_release_artifact.py::test_release_dependencies_install_without_an_index PASSED
AssertionError: assert [] == ['</.well-known/api-catalog>; rel="api-catalog"', '</guides/nhe-delegated-tokens/>; rel="service-doc"', '</guides/sso-integration-guide/>; rel="service-doc"', '</security/recommended-secure-configuration/>; rel="service-doc"']
```

Cause verified: the release archive omitted `docs/_headers`, so an extracted native runtime loaded no root links and `/` answered without the expected `Link` headers.

Fix:

- `scripts/build-release.sh` now creates `docs/` in the release tree and copies `docs/_headers` into `docs/_headers`, which is one of the exact runtime input paths `server/app.py` checks through `header_candidates()`.
- `server/tests/test_release_artifact.py` now imports the extracted release's own `server/app.py`, serves `/` from that extracted tree, and asserts the runtime `Link` headers match the committed `_headers` content.

### Review Item 2: Skip Docker-only proof when Docker is unavailable

Fix:

- `server/tests/test_release_artifact.py` now follows the existing convention:

```python
requires_docker = pytest.mark.skipif(
    shutil.which("docker") is None,
    reason="docker CLI is not available",
)
```

- The offline-install proof remains unchanged when Docker exists, including the `linux/amd64` runtime needed on Apple Silicon to validate the x86_64 wheels.

### GREEN Evidence For Review Fixes

Command:

```bash
.venv/bin/python -m pytest server/tests/test_release_artifact.py -v server/tests/test_app.py::test_serves_authenticated_root_with_link_headers -v
```

Result:

```text
collected 5 items
server/tests/test_release_artifact.py::test_release_contains_site_server_lock_and_wheelhouse PASSED
server/tests/test_release_artifact.py::test_release_preserves_root_link_header_behavior PASSED
server/tests/test_release_artifact.py::test_release_checksum_matches_archive PASSED
server/tests/test_release_artifact.py::test_release_dependencies_install_without_an_index PASSED
server/tests/test_app.py::test_serves_authenticated_root_with_link_headers PASSED
========================= 5 passed, 1 warning in 5.35s =========================
```

### Self-Review For Review Fixes

- Scope stayed narrow: only the release builder, the focused release test file, and the report changed.
- The new test proves behavior from the extracted artifact rather than from the source tree, so it cannot pass by accidentally reading the repository's own `docs/_headers`.
- Dockerless contributors can now run the focused file without a hard failure, while Docker-enabled environments still exercise the Linux x86_64 offline-install proof.

## Compliance Fix Addendum

### Issue: unplanned top-level `docs/` archive root

Requirement verified: the release must not add a new top-level `docs/` root just to carry `_headers`; instead it must package the source `docs/_headers` as `site/_headers`, which `header_candidates()` already consumes, and the release layout test must lock the exact allowed root set.

RED command:

```bash
.venv/bin/python -m pytest server/tests/test_release_artifact.py -v
```

RED result:

```text
collected 4 items
server/tests/test_release_artifact.py::test_release_contains_site_server_lock_and_wheelhouse FAILED
server/tests/test_release_artifact.py::test_release_preserves_root_link_header_behavior PASSED
server/tests/test_release_artifact.py::test_release_checksum_matches_archive PASSED
server/tests/test_release_artifact.py::test_release_dependencies_install_without_an_index PASSED
AssertionError: assert {'docs', 'requirements.txt', 'server', 'site', 'wheelhouse'} == {'requirements.txt', 'server', 'site', 'wheelhouse'}
```

Fix:

- `server/tests/test_release_artifact.py` now asserts the exact top-level root set `{"requirements.txt", "server", "site", "wheelhouse"}` and requires `site/_headers` to be present.
- `scripts/build-release.sh` now copies `docs/_headers` to `site/_headers` after `mkdocs build`, removing the extra top-level `docs/` root while preserving the extracted-app `Link` header behavior proof.

GREEN command:

```bash
.venv/bin/python -m pytest server/tests/test_release_artifact.py -v server/tests/test_app.py::test_serves_authenticated_root_with_link_headers -v
```

GREEN result:

```text
collected 5 items
server/tests/test_release_artifact.py::test_release_contains_site_server_lock_and_wheelhouse PASSED
server/tests/test_release_artifact.py::test_release_preserves_root_link_header_behavior PASSED
server/tests/test_release_artifact.py::test_release_checksum_matches_archive PASSED
server/tests/test_release_artifact.py::test_release_dependencies_install_without_an_index PASSED
server/tests/test_app.py::test_serves_authenticated_root_with_link_headers PASSED
========================= 5 passed, 1 warning in 5.26s =========================
```

Self-review:

- Scope stayed narrow to the release builder, the focused release test, and this report.
- The layout test now prevents future top-level root drift while the extracted-app test still proves real root `Link` header behavior from the packaged artifact.
