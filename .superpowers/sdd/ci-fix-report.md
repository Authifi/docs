# LSA-10037 CI Portability Fix Report

## Scope

Fix the three failing Linux CI tests in PR 49 without changing production's
fail-closed loopback validation and without pushing any branch updates.

## Diagnosis

### Failure 1: release tree permissions

- Root cause: the installer trusted the modes left behind by `python -m venv`.
  CPython copies activation templates with their packaged mode bits, so a
  writable template can bypass the installer's `umask`.
- Why CI saw it: GitHub's Python 3.12 templates produced writable activation
  files, while this machine's Ubuntu Noble packaging did not. The test was
  therefore answering the host packaging, not the installer invariant.
- Fix: make the test deterministic with an `AUTHIFI_DOCS_PYTHON_BIN` shim that
  delegates to the real Python and then widens `bin/activate*` after
  `-m venv`, and harden the installer with `chmod -R go-w "$candidate"` after
  tar extraction, venv creation, and offline pip install complete.

### Failures 2-3: local smoke lifecycle orchestration

- Root cause: the two orchestration tests inherited CI's job-wide
  `MOCK_OIDC_HOST=oidc-mock.local.test` before the later `/etc/hosts` step,
  then called `local_smoke.main()`, which validated the inherited issuer host
  with the real resolver and failed before the tests reached their actual
  event-order assertions.
- Fix: leave production validation unchanged, but make both orchestration tests
  explicitly set `MOCK_OIDC_HOST` and monkeypatch `compose_env_for_args` with a
  wrapper that calls the real function through its existing `resolve=` seam,
  resolving only `oidc-mock.local.test` to `127.0.0.1`.

## RED

| Command | Result |
| --- | --- |
| `.venv/bin/python -m pytest -q server/tests/test_deploy_release.py::test_the_installed_release_tree_is_never_group_or_other_writable` | `1 passed, 1 warning` on this machine before the shim, confirming the original test depended on local packaging rather than the invariant |
| `MOCK_OIDC_HOST=oidc-mock.local.test .venv/bin/python -m pytest -q server/tests/test_local_smoke.py::test_a_failed_smoke_dumps_diagnostics_before_tearing_down server/tests/test_local_smoke.py::test_a_passing_smoke_dumps_no_diagnostics` | `2 failed, 1 warning` with `ValueError: --mock-issuer: must name a host that resolves only to loopback` |
| `.venv/bin/python -m pytest -q server/tests/test_deploy_release.py::test_the_installed_release_tree_is_never_group_or_other_writable` | `1 failed, 1 warning` after adding the shim but before the installer fix; activation files were reported writable, e.g. `.venv/bin/activate: 0o666` |

## GREEN

| Command | Result |
| --- | --- |
| `.venv/bin/python -m pytest -q server/tests/test_deploy_release.py::test_the_installed_release_tree_is_never_group_or_other_writable` | `1 passed, 1 warning` |
| `MOCK_OIDC_HOST=oidc-mock.local.test .venv/bin/python -m pytest -q server/tests/test_local_smoke.py::test_a_failed_smoke_dumps_diagnostics_before_tearing_down server/tests/test_local_smoke.py::test_a_passing_smoke_dumps_no_diagnostics` | `2 passed, 1 warning` |
| `.venv/bin/python -m pytest -q server/tests/test_deploy_release.py` | `23 passed, 1 warning in 69.03s` |
| `MOCK_OIDC_HOST=oidc-mock.local.test .venv/bin/python -m pytest -q server/tests/test_local_smoke.py` | `51 passed, 1 warning` |
| `.venv/bin/python -m pytest -q server/tests/test_smoke_overrides.py` | `87 passed, 1 warning` |
| `shellcheck infra/scripts/deploy-release.sh scripts/*.sh` | clean |
| `.venv/bin/python -m pytest -q` | `1162 passed, 1 warning in 119.69s` |

## Files Changed

- `infra/scripts/deploy-release.sh`
- `server/tests/test_deploy_release.py`
- `server/tests/test_local_smoke.py`

## Notes

- No production loopback validation behavior changed.
- No reset, force-push, or push was performed.
