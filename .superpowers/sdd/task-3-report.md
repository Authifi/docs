# Task 3 Report: Locked Atomic Release Installer

## Status

Implemented the instance-side locked atomic release installer, added deploy
artifact bundling for provenance, and covered the installer with focused
filesystem-level integration tests.

## Files Changed

- `infra/scripts/deploy-release.sh`
- `scripts/build-release.sh`
- `server/tests/test_deploy_release.py`
- `server/tests/test_release_artifact.py`

## RED Evidence

### RED 1: installer missing

Command:

```bash
.venv/bin/python -m pytest server/tests/test_deploy_release.py \
  -k successful_install_switches_current_only_after_candidate_health -v
```

Observed failure:

```text
E   FileNotFoundError: [Errno 2] No such file or directory:
E   '/Users/keats.kirsch/Documents/GitHub/authifi-docs-wt/lsa-10037-aws-oidc/infra/scripts/deploy-release.sh'
```

This established the initial red state for the new installer contract before
any production script existed.

### RED 2: full failure-mode suite still red before installer existed

Command:

```bash
.venv/bin/python -m pytest server/tests/test_deploy_release.py -v
```

Observed result:

```text
Exit code: 1
```

The suite remained red with the same missing-script root cause after adding the
checksum, candidate-health, active-health rollback, lock, rollback, and
retention tests.

### RED 3: checksum failure exposed wrong exit propagation

Command:

```bash
.venv/bin/python -m pytest server/tests/test_deploy_release.py \
  -k bad_checksum_preserves_current -v
```

Observed failure:

```text
E       AssertionError: assert 0 != 0
E        +  where 0 = CompletedProcess(..., returncode=0, stderr='checksum mismatch for 2222...tar.gz\n').returncode
```

Root cause: the lock wrapper negated the child exit status and turned internal
installer failures into success.

### RED 4: release artifact missing `deploy/`

Command:

```bash
.venv/bin/python -m pytest server/tests/test_release_artifact.py \
  -k release_contains_site_server_lock_and_wheelhouse -v
```

Observed failure:

```text
E       AssertionError: assert {'requirements.txt', 'server', 'site', 'wheelhouse'}
E        == {'deploy', 'requirements.txt', 'server', 'site', 'wheelhouse'}
E       Extra items in the right set:
E       'deploy'
```

## GREEN Evidence

### Targeted greens

- Happy path:

  ```bash
  .venv/bin/python -m pytest server/tests/test_deploy_release.py \
    -k successful_install_switches_current_only_after_candidate_health -v
  ```

  Result: `1 passed, 6 deselected`

- Checksum preservation:

  ```bash
  .venv/bin/python -m pytest server/tests/test_deploy_release.py \
    -k bad_checksum_preserves_current -v
  ```

  Result: `1 passed, 6 deselected`

- Candidate-health preservation:

  ```bash
  .venv/bin/python -m pytest server/tests/test_deploy_release.py \
    -k failed_candidate_preserves_current -v
  ```

  Result: `1 passed, 6 deselected`

- Active-health rollback:

  ```bash
  .venv/bin/python -m pytest server/tests/test_deploy_release.py \
    -k failed_active_health_restores_previous_release -v
  ```

  Result: `1 passed, 6 deselected`

- Lock handling:

  ```bash
  .venv/bin/python -m pytest server/tests/test_deploy_release.py \
    -k lock_prevents_concurrent_install -v
  ```

  Result: `1 passed, 6 deselected`

- Explicit rollback:

  ```bash
  .venv/bin/python -m pytest server/tests/test_deploy_release.py \
    -k explicit_older_sha_is_a_normal_rollback -v
  ```

  Result: `1 passed, 6 deselected`

- Retention:

  ```bash
  .venv/bin/python -m pytest server/tests/test_deploy_release.py \
    -k successful_install_keeps_only_three_releases -v
  ```

  Result: `1 passed, 6 deselected`

- Release bundle root update:

  ```bash
  .venv/bin/python -m pytest server/tests/test_release_artifact.py \
    -k release_contains_site_server_lock_and_wheelhouse -v
  ```

  Result: `1 passed, 3 deselected`

### Final verification

Command:

```bash
.venv/bin/python -m pytest \
  server/tests/test_deploy_release.py \
  server/tests/test_release_artifact.py -v
shellcheck infra/scripts/deploy-release.sh scripts/build-release.sh
```

Observed result:

```text
======================== 11 passed, 1 warning in 27.99s ========================
```

ShellCheck was rerun after suppressing the dynamic-source `SC1091` info lines
for the environment files; the final ShellCheck invocation returned exit code 0.

## Self-Review

- The installer does not move `current` until the candidate health probe passes.
- If the post-restart active health probe fails, the previous symlink target is
  restored before the second restart, so the earlier release remains recoverable.
- Locking uses real kernel `flock` semantics through Python `fcntl.flock`,
  which keeps the behavior testable on macOS without depending on GNU `flock`.
- The release archive contract now includes a provenance copy at
  `deploy/deploy-release.sh`, and the exact-root artifact test was updated in
  lockstep to require `deploy`.

## Concerns

- Terraform wiring was intentionally left untouched per the task brief.
- The focused test slice still emits one pre-existing `DeprecationWarning` from
  `starlette.testclient`; it is unrelated to this task.
- I did not add coverage for malformed checksum-file formatting or redeploying
  the already-active SHA, since those behaviors were outside the requested Task
  3 brief.
