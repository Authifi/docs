# EC2 OIDC Hosting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the App Runner/ECR production deployment with an HTTPS ALB forwarding to one private EC2 instance that runs the OIDC-protected MkDocs server under `systemd`, deployed as immutable S3 releases through SSM.

**Architecture:** GitHub-hosted Actions builds the site and an offline Python wheelhouse, stores a checksum-addressed release in private S3, and invokes a release installer on EC2 through SSM. Terraform provisions the ALB, private instance, encrypted EBS, VPC endpoints, IAM, ACM, and release bucket; production uses neither Docker nor a self-hosted runner.

**Tech Stack:** Python 3.12, Starlette, Authlib, Uvicorn, MkDocs, Bash, `systemd`, Terraform AWS provider, EC2, ALB, S3, SSM, ACM, GitHub Actions OIDC.

## Global Constraints

- Preserve all existing public/protected path, session, logout, callback, traversal, content-type, and security-header behavior.
- The production EC2 instance has no public IP, no SSH ingress, and no general internet dependency for application deployment.
- The application runs as an unprivileged `authifi-docs` service account.
- Authifi production registration uses authorization code flow, PKCE S256, and `token_endpoint_auth_method=none`.
- `OIDC_CLIENT_SECRET` remains supported only as an optional compatibility mode.
- The Starlette session secret is generated on EC2, persisted root-only with mode `0600`, and never enters Git, Terraform state, user data, S3, or GitHub.
- Release object names use the Git commit SHA and are reused only when their SHA-256 checksum matches.
- Deployment is serialized, atomic, health-checked, and restores the previous release after a failed switch.
- Docker Compose remains optional local mock-OIDC tooling but is absent from production deployment and CI release paths.
- Every commit message starts with `LSA-10037`.

---

## File Structure

### Create

- `infra/templates/user-data.sh.tftpl` — stable host bootstrap only: service user, directories, environment, session key, installer, and `systemd` unit.
- `infra/scripts/deploy-release.sh` — instance-side locked installer, candidate check, atomic switch, and rollback.
- `scripts/build-release.sh` — deterministic site, wheelhouse, archive, and checksum builder.
- `server/tests/hcl_support.py` — small HCL text-parsing helpers shared by infrastructure tests.
- `server/tests/test_ec2_infra.py` — network, ALB, EC2, endpoint, storage, IAM, and bootstrap assertions.
- `server/tests/test_release_artifact.py` — release layout, checksum, and offline-install assertions.
- `server/tests/test_deploy_release.py` — installer success, locking, preservation, rollback, and pruning tests.
- `server/tests/test_deploy_workflow.py` — production workflow assertions.

### Replace or substantially rewrite

- `infra/main.tf` — replace ECR/App Runner with ALB/private EC2/S3/SSM architecture.
- `infra/variables.tf` — replace image/service variables with VPC, instance, release, and certificate inputs.
- `infra/outputs.tf` — output ALB, ACM, instance, bucket, target-group, and deploy-role values.
- `infra/terraform.tfvars.example` — provide non-secret EC2 architecture examples.
- `infra/README.md` — document bootstrap, DNS, deploy, verification, and rollback.
- `.github/workflows/deploy.yml` — build release, upload S3, invoke SSM, wait for target health, and probe the site.

### Modify

- `server/app.py` — make the client secret optional and explicitly register public-client token authentication.
- `server/tests/test_app.py` — cover absent and empty secret configuration.
- `server/tests/test_concurrent_logins.py` — prove public-client token exchange and PKCE.
- `compose.real.yaml` — accept an omitted local OIDC client secret.
- `.github/workflows/ci.yml` — verify release construction and offline dependency installation without production Docker probes.
- `README.md`, `CONTRIBUTING.md`, `docs/operations/aws-oidc-hosting.md` — describe the final architecture and commands.
- `.changeset/aws-oidc-hosting.md` — replace the App Runner claim.
- `Makefile` — add a local release-build target while retaining optional local Compose targets.

### Delete

- `server/tests/test_iam_pass_role.py` — App Runner-specific `iam:PassRole` tests after moving reusable helpers.

---

### Task 1: Public OIDC Client Mode

**Files:**
- Modify: `server/app.py`
- Modify: `server/tests/test_app.py`
- Modify: `compose.real.yaml`

**Interfaces:**
- Consumes: existing `AppConfig.from_env()`, `create_auth_client(config)`, and Authlib OAuth registration.
- Produces: `AppConfig.oidc_client_secret: str | None`; an absent or empty `OIDC_CLIENT_SECRET` registers `token_endpoint_auth_method="none"`, while a non-empty value retains `client_secret_basic`.

- [ ] **Step 1: Write failing environment tests**

Add to `server/tests/test_app.py`:

```python
@pytest.mark.parametrize("secret", [None, ""])
def test_oidc_client_secret_is_optional_for_a_pkce_public_client(
    monkeypatch: pytest.MonkeyPatch,
    secret: str | None,
) -> None:
    values = {
        "OIDC_ISSUER": "https://issuer.example.com",
        "OIDC_CLIENT_ID": "docs",
        "SESSION_SECRET": "session-secret",
        "PUBLIC_BASE_URL": "https://docs.example.com",
        "SITE_DIR": "/tmp/site",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    if secret is None:
        monkeypatch.delenv("OIDC_CLIENT_SECRET", raising=False)
    else:
        monkeypatch.setenv("OIDC_CLIENT_SECRET", secret)

    assert AppConfig.from_env().oidc_client_secret is None
```

- [ ] **Step 2: Run the focused test and confirm the red state**

Run:

```bash
.venv/bin/python -m pytest server/tests/test_app.py \
  -k oidc_client_secret_is_optional -v
```

Expected: FAIL with `KeyError: 'OIDC_CLIENT_SECRET'` for the missing case.

- [ ] **Step 3: Write failing Authlib registration tests**

Add to `server/tests/test_app.py`:

```python
@pytest.mark.parametrize(
    ("secret", "expected_method"),
    [(None, "none"), ("confidential-secret", "client_secret_basic")],
)
def test_auth_client_selects_token_authentication_from_secret_presence(
    secret: str | None,
    expected_method: str,
    site_dir: Path,
) -> None:
    config = AppConfig(
        oidc_issuer="https://issuer.example.com",
        oidc_client_id="docs",
        oidc_client_secret=secret,
        session_secret="session-secret",
        public_base_url="https://docs.example.com",
        site_dir=site_dir,
    )
    client = create_auth_client(config)
    assert client.client_kwargs["token_endpoint_auth_method"] == expected_method
```

Update the existing real-Authlib PKCE test to construct the same public-client
configuration before calling `create_app`:

```python
config = AppConfig(
    oidc_issuer="https://issuer.example.com",
    oidc_client_id="docs",
    oidc_client_secret=None,
    session_secret="session-secret",
    public_base_url="https://docs.example.com",
    site_dir=site_dir,
)
app = create_app(config)
```

- [ ] **Step 4: Run the new authentication tests and confirm they fail**

Run:

```bash
.venv/bin/python -m pytest \
  server/tests/test_app.py \
  -k "public_client or token_authentication or oidc_client_secret_is_optional" -v
```

Expected: FAIL because the dataclass and registration still require a string secret.

- [ ] **Step 5: Implement optional-secret configuration**

Change the `AppConfig` field and `from_env` assignment in `server/app.py`:

```python
oidc_client_secret: str | None
```

```python
oidc_client_secret=env.get("OIDC_CLIENT_SECRET") or None,
```

Register the Authlib client explicitly:

```python
token_auth_method = (
    "client_secret_basic" if config.oidc_client_secret else "none"
)
oauth.register(
    name="authifi",
    client_id=config.oidc_client_id,
    client_secret=config.oidc_client_secret,
    server_metadata_url=build_public_url(
        config.oidc_issuer.rstrip("/"),
        "/.well-known/openid-configuration",
    ),
    client_kwargs={
        "scope": DEFAULT_OIDC_SCOPE,
        "code_challenge_method": "S256",
        "token_endpoint_auth_method": token_auth_method,
    },
)
```

Relax the real local Compose overlay:

```yaml
services:
  docs:
    environment:
      OIDC_ISSUER: ${OIDC_ISSUER:?Set OIDC_ISSUER}
      OIDC_CLIENT_ID: ${OIDC_CLIENT_ID:?Set OIDC_CLIENT_ID}
      OIDC_CLIENT_SECRET: ${OIDC_CLIENT_SECRET:-}
      SESSION_SECRET: ${SESSION_SECRET:?Set SESSION_SECRET}
```

- [ ] **Step 6: Run server and Compose tests**

Run:

```bash
.venv/bin/python -m pytest \
  server/tests/test_app.py \
  server/tests/test_concurrent_logins.py \
  server/tests/test_compose.py -q
docker compose -f compose.yaml -f compose.real.yaml config --quiet
```

Expected: all selected tests pass and Compose exits 0.

- [ ] **Step 7: Commit**

```bash
git add server/app.py server/tests/test_app.py \
  compose.real.yaml
git commit -m "LSA-10037 support a PKCE public OIDC client"
```

---

### Task 2: Self-Contained Release Builder

**Files:**
- Create: `scripts/build-release.sh`
- Create: `server/tests/test_release_artifact.py`
- Modify: `Makefile`

**Interfaces:**
- Consumes: repository root, `server/requirements.txt`, Python 3.12, and a commit SHA.
- Produces: `<output>/<sha>.tar.gz` and `<output>/<sha>.tar.gz.sha256`; archive roots `site/`, `server/`, `requirements.txt`, and `wheelhouse/`.

- [ ] **Step 1: Write failing release-layout tests**

Create `server/tests/test_release_artifact.py`:

```python
from __future__ import annotations

import hashlib
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / "scripts" / "build-release.sh"


@pytest.fixture(scope="module")
def release(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str]:
    output = tmp_path_factory.mktemp("release")
    sha = "1" * 40
    subprocess.run(
        [str(BUILDER), sha, str(output)],
        cwd=ROOT,
        check=True,
        env={**os.environ, "SOURCE_DATE_EPOCH": "0"},
    )
    return output, sha


def test_release_contains_site_server_lock_and_wheelhouse(
    release: tuple[Path, str],
) -> None:
    output, sha = release
    with tarfile.open(output / f"{sha}.tar.gz") as archive:
        names = set(archive.getnames())
    assert "site/index.html" in names
    assert "server/app.py" in names
    assert "server/main.py" in names
    assert "requirements.txt" in names
    assert any(name.startswith("wheelhouse/") and name.endswith(".whl") for name in names)


def test_release_checksum_matches_archive(release: tuple[Path, str]) -> None:
    output, sha = release
    archive = output / f"{sha}.tar.gz"
    expected = (output / f"{sha}.tar.gz.sha256").read_text().split()[0]
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == expected


def test_release_dependencies_install_without_an_index(
    release: tuple[Path, str],
    tmp_path: Path,
) -> None:
    output, sha = release
    extracted = tmp_path / "release"
    with tarfile.open(output / f"{sha}.tar.gz") as archive:
        archive.extractall(extracted, filter="data")
    venv = tmp_path / "venv"
    subprocess.run(["python3.12", "-m", "venv", str(venv)], check=True)
    subprocess.run(
        [
            str(venv / "bin" / "pip"),
            "install",
            "--no-index",
            "--find-links",
            str(extracted / "wheelhouse"),
            "-r",
            str(extracted / "requirements.txt"),
        ],
        check=True,
    )
```

- [ ] **Step 2: Run the tests and confirm the builder is missing**

Run:

```bash
.venv/bin/python -m pytest server/tests/test_release_artifact.py -v
```

Expected: ERROR with `FileNotFoundError` for `scripts/build-release.sh`.

- [ ] **Step 3: Implement the release builder**

Create executable `scripts/build-release.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

sha="${1:?usage: build-release.sh SHA OUTPUT_DIR}"
output_dir="${2:?usage: build-release.sh SHA OUTPUT_DIR}"
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || {
  echo "SHA must be 40 lowercase hexadecimal characters" >&2
  exit 2
}

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
release="$work/release"
mkdir -p "$release/server" "$release/wheelhouse" "$output_dir"

"${PYTHON:-python3.12}" -m mkdocs build --strict --config-file "$root/mkdocs.yml" \
  --site-dir "$release/site"
cp "$root/server/app.py" "$root/server/main.py" "$root/server/__init__.py" \
  "$release/server/"
cp "$root/server/requirements.txt" "$release/requirements.txt"

"${PYTHON:-python3.12}" -m pip download \
  --requirement "$root/server/requirements.txt" \
  --dest "$release/wheelhouse" \
  --only-binary=:all: \
  --platform manylinux_2_17_x86_64 \
  --implementation cp \
  --python-version 312

archive="$output_dir/$sha.tar.gz"
"${PYTHON:-python3.12}" - "$release" "$archive" <<'PY'
import gzip
import os
import sys
import tarfile
from pathlib import Path

source = Path(sys.argv[1])
destination = Path(sys.argv[2])
epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
with destination.open("wb") as raw:
    with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=epoch) as zipped:
        with tarfile.open(fileobj=zipped, mode="w") as archive:
            for path in sorted(source.rglob("*")):
                name = path.relative_to(source).as_posix()
                info = archive.gettarinfo(str(path), arcname=name)
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mtime = epoch
                if path.is_file():
                    with path.open("rb") as stream:
                        archive.addfile(info, stream)
                else:
                    archive.addfile(info)
PY
(
  cd "$output_dir"
  shasum -a 256 "$(basename "$archive")" > "$(basename "$archive").sha256"
)
```

Add to `Makefile`:

```make
release:
	./scripts/build-release.sh "$${RELEASE_SHA:-$$(git rev-parse HEAD)}" \
		"$${RELEASE_DIR:-dist/releases}"
```

- [ ] **Step 4: Run release tests**

Run:

```bash
chmod +x scripts/build-release.sh
.venv/bin/python -m pytest server/tests/test_release_artifact.py -v
```

Expected: all tests pass, including `pip install --no-index`.

- [ ] **Step 5: Commit**

```bash
git add scripts/build-release.sh server/tests/test_release_artifact.py Makefile
git commit -m "LSA-10037 build self-contained S3 releases"
```

---

### Task 3: Locked Atomic Release Installer

**Files:**
- Create: `infra/scripts/deploy-release.sh`
- Create: `server/tests/test_deploy_release.py`

**Interfaces:**
- Consumes: `deploy-release.sh SHA`, files placed by the deployment SSM
  document at `/opt/authifi-docs/incoming/SHA/SHA.tar.gz{,.sha256}`,
  `/etc/authifi-docs/environment`, and `systemctl`.
- Produces: release-local `.venv`, atomic `/opt/authifi-docs/current` symlink,
  rollback on failed active health, and at most three local releases.

- [ ] **Step 1: Write a fake-command integration harness and failing success test**

Create `server/tests/test_deploy_release.py` with a temporary root and fake
`systemctl` and `curl` commands. Use the operating system's real `flock`; the
first test must execute the real script:

```python
def test_successful_install_switches_current_only_after_candidate_health(
    deploy_harness: DeployHarness,
) -> None:
    old = deploy_harness.make_release("0" * 40)
    deploy_harness.current.symlink_to(old)
    new_sha = "1" * 40
    deploy_harness.publish_archive(new_sha)

    result = deploy_harness.run(new_sha)

    assert result.returncode == 0
    assert deploy_harness.current.resolve().name == new_sha
    assert deploy_harness.events.index("candidate-health") < deploy_harness.events.index(
        "systemctl:restart"
    )
    assert "active-health" in deploy_harness.events
```

The fixture sets overridable paths consumed by the script:

```python
env.update(
    AUTHIFI_DOCS_ROOT=str(root),
    AUTHIFI_DOCS_ETC=str(etc),
    AUTHIFI_DOCS_LOCK=str(root / "deploy.lock"),
    PATH=f"{fake_bin}:{env['PATH']}",
)
```

- [ ] **Step 2: Add failing failure-mode tests**

Add:

```python
def test_bad_checksum_preserves_current(deploy_harness: DeployHarness) -> None:
    old = deploy_harness.seed_active_release()
    sha = "2" * 40
    deploy_harness.publish_archive(sha, checksum="0" * 64)
    assert deploy_harness.run(sha).returncode != 0
    assert deploy_harness.current.resolve() == old


def test_failed_candidate_preserves_current(deploy_harness: DeployHarness) -> None:
    old = deploy_harness.seed_active_release()
    sha = "3" * 40
    deploy_harness.publish_archive(sha)
    deploy_harness.fail_candidate_health = True
    assert deploy_harness.run(sha).returncode != 0
    assert deploy_harness.current.resolve() == old


def test_failed_active_health_restores_previous_release(
    deploy_harness: DeployHarness,
) -> None:
    old = deploy_harness.seed_active_release()
    sha = "4" * 40
    deploy_harness.publish_archive(sha)
    deploy_harness.fail_active_health_once = True
    assert deploy_harness.run(sha).returncode != 0
    assert deploy_harness.current.resolve() == old
    assert deploy_harness.events.count("systemctl:restart") == 2


def test_lock_prevents_concurrent_install(deploy_harness: DeployHarness) -> None:
    with deploy_harness.hold_lock():
        result = deploy_harness.run("5" * 40)
    assert result.returncode == 75
    assert "deployment already running" in result.stderr


def test_explicit_older_sha_is_a_normal_rollback(
    deploy_harness: DeployHarness,
) -> None:
    older = "6" * 40
    newer = "7" * 40
    deploy_harness.publish_archive(older)
    deploy_harness.publish_archive(newer)
    assert deploy_harness.run(newer).returncode == 0
    assert deploy_harness.run(older).returncode == 0
    assert deploy_harness.current.resolve().name == older
```

- [ ] **Step 3: Run installer tests and confirm they fail**

Run:

```bash
.venv/bin/python -m pytest server/tests/test_deploy_release.py -v
```

Expected: ERROR because `infra/scripts/deploy-release.sh` does not exist.

- [ ] **Step 4: Implement the installer**

Create executable `infra/scripts/deploy-release.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

sha="${1:?usage: deploy-release.sh SHA}"
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || exit 2

root="${AUTHIFI_DOCS_ROOT:-/opt/authifi-docs}"
etc_dir="${AUTHIFI_DOCS_ETC:-/etc/authifi-docs}"
lock="${AUTHIFI_DOCS_LOCK:-/run/lock/authifi-docs-deploy.lock}"
releases="$root/releases"
current="$root/current"
incoming="$root/incoming/$sha"
archive="$incoming/$sha.tar.gz"

mkdir -p "$releases" "$incoming"
exec 9>"$lock"
flock -n 9 || {
  echo "deployment already running" >&2
  exit 75
}

[[ -s "$archive" && -s "$archive.sha256" ]] || {
  echo "SSM did not stage the release archive and checksum" >&2
  exit 1
}
(
  cd "$incoming"
  sha256sum --check "$(basename "$archive").sha256"
)

candidate="$releases/$sha"
rm -rf "$candidate"
mkdir -p "$candidate"
tar -xzf "$archive" -C "$candidate"
python3 -m venv "$candidate/.venv"
"$candidate/.venv/bin/pip" install --no-index \
  --find-links "$candidate/wheelhouse" \
  -r "$candidate/requirements.txt"

set -a
# shellcheck disable=SC1090
source "$etc_dir/environment"
# shellcheck disable=SC1090
source "$etc_dir/session.env"
set +a

SITE_DIR="$candidate/site" timeout 30 \
  "$candidate/.venv/bin/uvicorn" server.main:app \
  --app-dir "$candidate" --host 127.0.0.1 --port 18080 &
candidate_pid=$!
trap 'kill "$candidate_pid" 2>/dev/null || true' EXIT
for _ in $(seq 1 30); do
  curl --fail --silent http://127.0.0.1:18080/health >/dev/null && break
  sleep 1
done
curl --fail --silent http://127.0.0.1:18080/health >/dev/null
kill "$candidate_pid"
wait "$candidate_pid" || true
trap - EXIT

previous=""
if [[ -L "$current" ]]; then
  previous="$(readlink "$current")"
fi
ln -sfn "$candidate" "$current.next"
mv -Tf "$current.next" "$current"
systemctl restart authifi-docs

if ! curl --fail --silent --retry 15 --retry-delay 1 \
  http://127.0.0.1:8080/health >/dev/null; then
  if [[ -n "$previous" ]]; then
    ln -sfn "$previous" "$current.next"
    mv -Tf "$current.next" "$current"
    systemctl restart authifi-docs
  fi
  echo "active release failed health check; previous release restored" >&2
  exit 1
fi

find "$releases" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' \
  | sort -nr | awk 'NR > 3 {sub(/^[^ ]+ /, ""); print}' \
  | xargs -r rm -rf
rm -rf "$incoming"
```

Run `chmod +x infra/scripts/deploy-release.sh` before executing the tests.

- [ ] **Step 5: Bundle the versioned installer in every release**

First add this assertion to
`test_release_contains_site_server_lock_and_wheelhouse` in
`server/tests/test_release_artifact.py`:

```python
assert "deploy/deploy-release.sh" in names
```

Run that test and expect it to fail. Then add to `scripts/build-release.sh`:

```bash
mkdir -p "$release/deploy"
cp "$root/infra/scripts/deploy-release.sh" "$release/deploy/deploy-release.sh"
```

This copy is for release provenance and auditability; the stable installer
invoked by SSM is installed through Terraform user data.

- [ ] **Step 6: Run installer and release tests plus ShellCheck**

Run:

```bash
.venv/bin/python -m pytest \
  server/tests/test_deploy_release.py \
  server/tests/test_release_artifact.py -v
shellcheck infra/scripts/deploy-release.sh scripts/build-release.sh
```

Expected: all tests pass and ShellCheck reports no findings.

- [ ] **Step 7: Commit**

```bash
git add infra/scripts/deploy-release.sh scripts/build-release.sh \
  server/tests/test_deploy_release.py server/tests/test_release_artifact.py
git commit -m "LSA-10037 deploy releases atomically through SSM"
```

---

### Task 4: ALB, Private EC2, S3, SSM, and Bootstrap Terraform

**Files:**
- Create: `server/tests/hcl_support.py`
- Create: `server/tests/test_ec2_infra.py`
- Create: `infra/templates/user-data.sh.tftpl`
- Rewrite: `infra/main.tf`
- Rewrite: `infra/variables.tf`
- Rewrite: `infra/outputs.tf`
- Rewrite: `infra/terraform.tfvars.example`
- Delete: `server/tests/test_iam_pass_role.py`

**Interfaces:**
- Consumes: `infra/scripts/deploy-release.sh`, Authifi issuer/client ID, canonical
  URL/domain, GitHub repository subject, and network sizing variables.
- Produces: Terraform outputs `release_bucket_name`, `instance_id`,
  `target_group_arn`, `ssm_document_name`, `alb_dns_name`, `alb_zone_id`,
  `certificate_validation_records`, and `github_deploy_role_arn`.

- [ ] **Step 1: Extract reusable HCL helpers without changing assertions**

Create `server/tests/hcl_support.py` and update the old App Runner tests to use
these source-oriented signatures:

```python
from __future__ import annotations

import re


def block_body(source: str, opening_brace: int) -> str:
    depth = 0
    for offset, character in enumerate(source[opening_brace:], start=opening_brace):
        depth += {"{": 1, "}": -1}.get(character, 0)
        if depth == 0:
            return source[opening_brace + 1 : offset]
    raise AssertionError(f"unbalanced braces from offset {opening_brace}")


def hcl_block(source: str, header: str) -> str:
    start = source.index(header)
    return block_body(source, source.index("{", start + len(header)))


def statements(document_body: str) -> list[str]:
    return [
        block_body(document_body, match.end() - 1)
        for match in re.finditer(r"\n\s*statement\s*\{", document_body)
    ]


def statement_with_action(document_body: str, action: str) -> str:
    matches = [block for block in statements(document_body) if f'"{action}"' in block]
    assert len(matches) == 1, (
        f"expected exactly one {action} statement, found {len(matches)}"
    )
    return matches[0]
```

In `test_iam_pass_role.py`, compute the document body once with
`hcl_block(MAIN_TF.read_text(), 'data "aws_iam_policy_document" "github_deploy"')`
and pass that body to `statement_with_action`. Test meanings and assertions
must remain unchanged.

Run:

```bash
.venv/bin/python -m pytest server/tests/test_iam_pass_role.py -q
```

Expected: 6 tests pass before the architecture-specific module is deleted.

- [ ] **Step 2: Write failing Terraform architecture tests**

Create `server/tests/test_ec2_infra.py`:

```python
from pathlib import Path

from server.tests.hcl_support import hcl_block, statement_with_action

ROOT = Path(__file__).resolve().parents[2]
MAIN = (ROOT / "infra" / "main.tf").read_text()
VARIABLES = (ROOT / "infra" / "variables.tf").read_text()
OUTPUTS = (ROOT / "infra" / "outputs.tf").read_text()
USER_DATA = (ROOT / "infra" / "templates" / "user-data.sh.tftpl").read_text()


def test_app_runner_and_ecr_are_absent() -> None:
    assert 'resource "aws_apprunner_' not in MAIN
    assert 'resource "aws_ecr_' not in MAIN
    assert "apprunner" not in VARIABLES.lower()
    assert "ecr_" not in VARIABLES.lower()


def test_instance_is_private_and_uses_encrypted_ebs() -> None:
    instance = hcl_block(MAIN, 'resource "aws_instance" "docs"')
    assert "associate_public_ip_address = false" in instance
    root = hcl_block(instance, "root_block_device")
    assert "encrypted = true" in root


def test_only_the_alb_security_group_can_reach_the_app_port() -> None:
    ingress = hcl_block(MAIN, 'resource "aws_vpc_security_group_ingress_rule" "app_from_alb"')
    assert "referenced_security_group_id = aws_security_group.alb.id" in ingress
    assert "from_port                    = var.app_port" in ingress
    assert 'cidr_ipv4' not in ingress


def test_alb_redirects_http_and_checks_application_health() -> None:
    http = hcl_block(MAIN, 'resource "aws_lb_listener" "http"')
    target = hcl_block(MAIN, 'resource "aws_lb_target_group" "docs"')
    assert 'status_code = "HTTP_301"' in http
    assert 'path                = "/health"' in hcl_block(target, "health_check")


def test_private_endpoints_cover_release_and_ssm_traffic() -> None:
    assert 'resource "aws_vpc_endpoint" "s3"' in MAIN
    for name in ("ssm", "ssmmessages", "ec2messages"):
        endpoint = hcl_block(MAIN, f'resource "aws_vpc_endpoint" "{name}"')
        assert 'vpc_endpoint_type = "Interface"' in endpoint
        assert "private_dns_enabled = true" in endpoint


def test_ssm_document_stages_both_objects_before_running_the_installer() -> None:
    document = hcl_block(MAIN, 'resource "aws_ssm_document" "deploy"')
    assert document.count('"aws:downloadContent"') == 2
    assert ".tar.gz" in document
    assert ".tar.gz.sha256" in document
    assert '"aws:runShellScript"' in document
    assert "/usr/local/sbin/authifi-docs-deploy" in document


def test_release_bucket_is_private_encrypted_versioned_and_expiring() -> None:
    assert 'resource "aws_s3_bucket_public_access_block" "releases"' in MAIN
    assert 'resource "aws_s3_bucket_server_side_encryption_configuration" "releases"' in MAIN
    assert 'resource "aws_s3_bucket_versioning" "releases"' in MAIN
    assert 'resource "aws_s3_bucket_lifecycle_configuration" "releases"' in MAIN


def test_deploy_role_is_limited_to_s3_ssm_and_target_health() -> None:
    policy = hcl_block(MAIN, 'data "aws_iam_policy_document" "github_deploy"')
    assert statement_with_action(policy, "s3:PutObject")
    assert statement_with_action(policy, "ssm:SendCommand")
    assert statement_with_action(policy, "ssm:GetCommandInvocation")
    assert statement_with_action(policy, "elasticloadbalancing:DescribeTargetHealth")
    for forbidden in ("ecr:", "apprunner:", "iam:PassRole"):
        assert forbidden not in policy


def test_bootstrap_creates_a_non_root_service_and_root_only_session_key() -> None:
    assert "User=authifi-docs" in USER_DATA
    assert "Group=authifi-docs" in USER_DATA
    assert "chmod 0600 /etc/authifi-docs/session.env" in USER_DATA
    assert "openssl rand -hex 32" in USER_DATA
    assert "SESSION_SECRET=" not in MAIN
```

- [ ] **Step 3: Run the new infrastructure tests and confirm the red state**

Run:

```bash
.venv/bin/python -m pytest \
  server/tests/test_ec2_infra.py \
  server/tests/test_public_boundary.py \
  -k "infra or terraform or post_logout" -v
```

Expected: FAIL because EC2 resources and the user-data template are absent.

- [ ] **Step 4: Replace Terraform variables**

Rewrite `infra/variables.tf` with validated inputs for:

```hcl
variable "aws_region" { type = string }
variable "service_name" { type = string; default = "authifi-docs" }
variable "vpc_cidr" { type = string; default = "10.42.0.0/16" }
variable "public_subnet_cidrs" {
  type    = list(string)
  default = ["10.42.0.0/24", "10.42.1.0/24"]
  validation {
    condition     = length(var.public_subnet_cidrs) == 2
    error_message = "Exactly two public subnet CIDRs are required for the ALB."
  }
}
variable "private_subnet_cidr" { type = string; default = "10.42.10.0/24" }
variable "instance_type" { type = string; default = "t3.micro" }
variable "root_volume_size_gib" { type = number; default = 20 }
variable "app_port" { type = number; default = 8080 }
variable "release_bucket_name" { type = string; default = null; nullable = true }
variable "release_retention_days" { type = number; default = 90 }
variable "oidc_issuer" { type = string }
variable "oidc_client_id" { type = string }
variable "public_base_url" { type = string }
variable "site_dir" { type = string; default = "/opt/authifi-docs/current/site" }
variable "post_logout_path" {
  type    = string
  default = "/privacy-policy/"
  # Preserve the existing three validation blocks byte-for-byte.
}
variable "custom_domain_name" { type = string; default = "docs.authifi.io" }
variable "enable_https_listener" {
  type        = bool
  default     = false
  description = "Enable redirect and HTTPS listeners after ACM DNS validation succeeds."
}
variable "existing_github_oidc_provider_arn" {
  type = string
  default = null
  nullable = true
}
variable "github_repository" { type = string; default = "Authifi/docs" }
variable "deploy_branch" { type = string; default = "main" }
variable "tags" { type = map(string); default = {} }
```

Do not shorten or merge the three existing `post_logout_path` validation
blocks; `test_public_boundary.py` checks their exact safety coverage.

- [ ] **Step 5: Implement networking, ALB, EC2, endpoints, and storage**

Rewrite `infra/main.tf` around these exact resource groups:

```hcl
data "aws_availability_zones" "available" { state = "available" }
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"]
  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }
  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
}

resource "aws_vpc" "docs" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
}
resource "aws_internet_gateway" "docs" { vpc_id = aws_vpc.docs.id }
resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.docs.id
  cidr_block              = var.public_subnet_cidrs[count.index]
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true
}
resource "aws_subnet" "app" {
  vpc_id                  = aws_vpc.docs.id
  cidr_block              = var.private_subnet_cidr
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = false
}
resource "aws_route_table" "public" { vpc_id = aws_vpc.docs.id }
resource "aws_route" "public_internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.docs.id
}
resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}
resource "aws_route_table" "app" { vpc_id = aws_vpc.docs.id }
resource "aws_route_table_association" "app" {
  subnet_id      = aws_subnet.app.id
  route_table_id = aws_route_table.app.id
}
```

Add separate ALB, app, and endpoint security groups; use
`aws_vpc_security_group_ingress_rule.app_from_alb` with
`referenced_security_group_id = aws_security_group.alb.id`. Permit endpoint
port 443 only from the app security group.

Add:

```hcl
resource "aws_lb" "docs" {
  name               = var.service_name
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id
}
resource "aws_lb_target_group" "docs" {
  name     = var.service_name
  port     = var.app_port
  protocol = "HTTP"
  vpc_id   = aws_vpc.docs.id
  health_check {
    enabled             = true
    path                = "/health"
    matcher             = "200"
    healthy_threshold   = 2
    unhealthy_threshold = 2
  }
}
resource "aws_acm_certificate" "docs" {
  domain_name       = var.custom_domain_name
  validation_method = "DNS"
}
resource "aws_lb_listener" "http" {
  count             = var.enable_https_listener ? 1 : 0
  load_balancer_arn = aws_lb.docs.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}
resource "aws_lb_listener" "https" {
  count             = var.enable_https_listener ? 1 : 0
  load_balancer_arn = aws_lb.docs.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate.docs.arn
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.docs.arn
  }
}
resource "aws_lb_listener" "bootstrap" {
  count             = var.enable_https_listener ? 0 : 1
  load_balancer_arn = aws_lb.docs.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      message_body = "HTTPS certificate validation is pending"
      status_code  = "503"
    }
  }
}
```

The two-stage listener switch is required because DNS is managed outside this
Terraform root: the first apply must create the certificate before its
validation records exist, and AWS refuses to attach a pending certificate to
an HTTPS listener. After DNS validation reports `ISSUED`, apply with
`enable_https_listener=true`; that atomically replaces the bootstrap response
with HTTP redirect and HTTPS forward listeners.

Create the private versioned release bucket with AES256 server-side
encryption, all four public-access-block flags, and expiration of current and
noncurrent objects after `var.release_retention_days`.

Create gateway S3 and interface `ssm`, `ssmmessages`, and `ec2messages`
endpoints. Associate S3 with `aws_route_table.app.id`; associate interface
endpoints with `aws_subnet.app.id`.

- [ ] **Step 6: Implement IAM and host bootstrap**

Create an EC2 trust role, attach
`arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore`,
grant only `s3:GetObject` on
`${aws_s3_bucket.releases.arn}/releases/*`, and create an instance profile.

Create `infra/templates/user-data.sh.tftpl`:

```bash
#!/usr/bin/env bash
set -euo pipefail

useradd --system --home-dir /opt/authifi-docs --shell /sbin/nologin authifi-docs || true
install -d -o authifi-docs -g authifi-docs /opt/authifi-docs/releases /opt/authifi-docs/incoming
install -d -m 0700 -o root -g root /etc/authifi-docs

cat > /etc/authifi-docs/environment <<'ENV'
OIDC_ISSUER=${oidc_issuer}
OIDC_CLIENT_ID=${oidc_client_id}
PUBLIC_BASE_URL=${public_base_url}
SITE_DIR=${site_dir}
POST_LOGOUT_PATH=${post_logout_path}
ENV
chmod 0600 /etc/authifi-docs/environment

if [[ ! -s /etc/authifi-docs/session.env ]]; then
  umask 077
  printf 'SESSION_SECRET=%s\n' "$(openssl rand -hex 32)" \
    > /etc/authifi-docs/session.env
fi
chmod 0600 /etc/authifi-docs/session.env

base64 -d > /usr/local/sbin/authifi-docs-deploy <<'SCRIPT'
${deploy_script_base64}
SCRIPT
chmod 0750 /usr/local/sbin/authifi-docs-deploy

cat > /etc/systemd/system/authifi-docs.service <<'UNIT'
[Unit]
Description=Authifi OIDC-protected documentation
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=authifi-docs
Group=authifi-docs
WorkingDirectory=/opt/authifi-docs/current
EnvironmentFile=/etc/authifi-docs/environment
EnvironmentFile=/etc/authifi-docs/session.env
ExecStart=/opt/authifi-docs/current/.venv/bin/uvicorn server.main:app --host 0.0.0.0 --port ${app_port}
Restart=on-failure
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/authifi-docs

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable authifi-docs.service
```

Pass `deploy_script_base64 =
base64encode(file("${path.module}/scripts/deploy-release.sh"))` to
`templatefile`. Create the EC2 instance with
`associate_public_ip_address = false`, IMDSv2 required, encrypted root EBS,
the app security group, the instance profile, and this rendered user data.
Attach it to the target group.

Create the deployment Command document so the SSM agent, rather than a
host-installed AWS CLI, stages both S3 objects under the path the installer
expects:

```hcl
resource "aws_ssm_document" "deploy" {
  name          = "${var.service_name}-deploy"
  document_type = "Command"
  content = jsonencode({
    schemaVersion = "2.2"
    description   = "Install an immutable Authifi docs release"
    parameters = {
      ReleaseSha = {
        type           = "String"
        allowedPattern = "^[0-9a-f]{40}$"
      }
    }
    mainSteps = [
      {
        action = "aws:downloadContent"
        name   = "downloadArchive"
        inputs = {
          sourceType = "S3"
          sourceInfo = jsonencode({
            path = "https://${aws_s3_bucket.releases.id}.s3.${var.aws_region}.amazonaws.com/releases/{{ ReleaseSha }}.tar.gz"
          })
          destinationPath = "/opt/authifi-docs/incoming/{{ ReleaseSha }}"
        }
      },
      {
        action = "aws:downloadContent"
        name   = "downloadChecksum"
        inputs = {
          sourceType = "S3"
          sourceInfo = jsonencode({
            path = "https://${aws_s3_bucket.releases.id}.s3.${var.aws_region}.amazonaws.com/releases/{{ ReleaseSha }}.tar.gz.sha256"
          })
          destinationPath = "/opt/authifi-docs/incoming/{{ ReleaseSha }}"
        }
      },
      {
        action = "aws:runShellScript"
        name   = "installRelease"
        inputs = {
          runCommand = [
            "/usr/local/sbin/authifi-docs-deploy '{{ ReleaseSha }}'",
          ]
        }
      },
    ]
  })
}
```

Rebuild `data.aws_iam_policy_document.github_deploy` with four scoped
capabilities:

```hcl
actions   = ["s3:GetObject", "s3:PutObject"]
resources = ["${aws_s3_bucket.releases.arn}/releases/*"]
```

```hcl
actions   = ["ssm:SendCommand"]
resources = [
  aws_ssm_document.deploy.arn,
  aws_instance.docs.arn,
]
```

Add `ssm:GetCommandInvocation` and `ssm:ListCommandInvocations` on `"*"`,
because those APIs do not support instance-level resource scoping. Add
`elasticloadbalancing:DescribeTargetHealth` on `"*"`, the API's required
resource scope. Retain the branch-bound GitHub OIDC trust policy.

- [ ] **Step 7: Replace outputs and example variables**

`infra/outputs.tf` must expose:

```hcl
output "aws_region" { value = var.aws_region }
output "release_bucket_name" { value = aws_s3_bucket.releases.id }
output "instance_id" { value = aws_instance.docs.id }
output "target_group_arn" { value = aws_lb_target_group.docs.arn }
output "ssm_document_name" { value = aws_ssm_document.deploy.name }
output "alb_dns_name" { value = aws_lb.docs.dns_name }
output "alb_zone_id" { value = aws_lb.docs.zone_id }
output "certificate_arn" { value = aws_acm_certificate.docs.arn }
output "certificate_validation_records" {
  value = [
    for option in aws_acm_certificate.docs.domain_validation_options : {
      name  = option.resource_record_name
      type  = option.resource_record_type
      value = option.resource_record_value
    }
  ]
}
output "github_deploy_role_arn" { value = aws_iam_role.github_deploy.arn }
output "github_oidc_provider_arn" {
  value = local.github_oidc_provider_arn
}
```

Update `infra/terraform.tfvars.example` with the new non-secret values and no
secret ARN, image, ECR, or App Runner variables.

- [ ] **Step 8: Delete App Runner-specific tests and run Terraform checks**

Delete `server/tests/test_iam_pass_role.py`.

Run:

```bash
.venv/bin/python -m pytest \
  server/tests/test_ec2_infra.py \
  server/tests/test_infra.py \
  server/tests/test_public_boundary.py -q
terraform -chdir=infra fmt -recursive
terraform -chdir=infra init -backend=false
terraform -chdir=infra validate
```

Expected: all tests pass and Terraform reports `Success! The configuration is valid.`

- [ ] **Step 9: Commit**

```bash
git add infra server/tests/hcl_support.py server/tests/test_ec2_infra.py \
  server/tests/test_iam_pass_role.py infra/terraform.tfvars.example
git commit -m "LSA-10037 provision private EC2 docs hosting"
```

---

### Task 5: S3 and SSM Production Deployment Workflow

**Files:**
- Rewrite: `.github/workflows/deploy.yml`
- Create: `server/tests/test_deploy_workflow.py`

**Interfaces:**
- Consumes repository variables `AWS_REGION`, `AWS_DEPLOY_ROLE_ARN`,
  `RELEASE_BUCKET_NAME`, `DOCS_INSTANCE_ID`, `DOCS_SSM_DOCUMENT_NAME`,
  `DOCS_TARGET_GROUP_ARN`, and `DOCS_PUBLIC_BASE_URL`.
- Produces an immutable S3 release, completed SSM command, healthy ALB target,
  public-page 200, and protected-page redirect to `/_auth/login`.

- [ ] **Step 1: Write failing workflow-policy tests**

Create `server/tests/test_deploy_workflow.py`:

```python
from pathlib import Path

WORKFLOW = (
    Path(__file__).resolve().parents[2] / ".github" / "workflows" / "deploy.yml"
).read_text()


def test_production_deploy_uses_github_oidc_s3_and_ssm() -> None:
    assert "permissions:" in WORKFLOW
    assert "id-token: write" in WORKFLOW
    assert "aws-actions/configure-aws-credentials" in WORKFLOW
    assert "./scripts/build-release.sh" in WORKFLOW
    assert "aws s3" in WORKFLOW
    assert "aws ssm send-command" in WORKFLOW
    assert "aws ssm get-command-invocation" in WORKFLOW
    assert "aws elbv2 describe-target-health" in WORKFLOW


def test_production_deploy_has_no_container_or_runner_dependency() -> None:
    for forbidden in (
        "self-hosted",
        "docker ",
        "docker/",
        "ghcr.io",
        "ecr",
        "apprunner",
    ):
        assert forbidden not in WORKFLOW.lower()


def test_rerun_reuses_only_an_identical_release() -> None:
    assert "sha256sum --check" in WORKFLOW
    assert "head-object" in WORKFLOW
    assert "checksum mismatch for existing release" in WORKFLOW


def test_live_probe_checks_public_and_protected_boundaries() -> None:
    assert "/privacy-policy/" in WORKFLOW
    assert "--max-redirs 0" in WORKFLOW
    assert "/guides/" in WORKFLOW
    assert "/_auth/login" in WORKFLOW
```

- [ ] **Step 2: Run workflow tests and confirm the red state**

Run:

```bash
.venv/bin/python -m pytest server/tests/test_deploy_workflow.py -v
```

Expected: FAIL because the workflow still uses ECR and App Runner.

- [ ] **Step 3: Rewrite the deploy workflow**

Use this job shape in `.github/workflows/deploy.yml`:

```yaml
name: Deploy docs

on:
  push:
    branches: [main]
  workflow_dispatch:
    inputs:
      release_sha:
        description: Existing commit SHA to deploy
        required: false
        type: string

permissions:
  contents: read
  id-token: write

concurrency:
  group: authifi-docs-production
  cancel-in-progress: false

jobs:
  deploy:
    runs-on: ubuntu-latest
    environment: production
    env:
      AWS_REGION: ${{ vars.AWS_REGION }}
      RELEASE_BUCKET_NAME: ${{ vars.RELEASE_BUCKET_NAME }}
      DOCS_INSTANCE_ID: ${{ vars.DOCS_INSTANCE_ID }}
      DOCS_SSM_DOCUMENT_NAME: ${{ vars.DOCS_SSM_DOCUMENT_NAME }}
      DOCS_TARGET_GROUP_ARN: ${{ vars.DOCS_TARGET_GROUP_ARN }}
      DOCS_PUBLIC_BASE_URL: ${{ vars.DOCS_PUBLIC_BASE_URL }}
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - run: python -m pip install -r requirements.txt
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ vars.AWS_DEPLOY_ROLE_ARN }}
          aws-region: ${{ env.AWS_REGION }}
      - name: Select release
        id: release
        shell: bash
        run: |
          set -euo pipefail
          sha="${{ inputs.release_sha || github.sha }}"
          [[ "$sha" =~ ^[0-9a-f]{40}$ ]]
          echo "sha=$sha" >> "$GITHUB_OUTPUT"
      - name: Build release
        if: ${{ inputs.release_sha == '' }}
        run: ./scripts/build-release.sh "${{ steps.release.outputs.sha }}" dist/releases
      - name: Publish or verify release
        if: ${{ inputs.release_sha == '' }}
        shell: bash
        run: |
          set -euo pipefail
          sha="${{ steps.release.outputs.sha }}"
          prefix="s3://$RELEASE_BUCKET_NAME/releases"
          if aws s3api head-object \
            --bucket "$RELEASE_BUCKET_NAME" \
            --key "releases/$sha.tar.gz" >/dev/null 2>&1; then
            aws s3 cp "$prefix/$sha.tar.gz.sha256" dist/releases/remote.sha256
            (cd dist/releases && sha256sum --check remote.sha256) || {
              echo "checksum mismatch for existing release" >&2
              exit 1
            }
          else
            aws s3 cp "dist/releases/$sha.tar.gz" "$prefix/$sha.tar.gz"
            aws s3 cp "dist/releases/$sha.tar.gz.sha256" "$prefix/$sha.tar.gz.sha256"
          fi
      - name: Install release through SSM
        id: command
        shell: bash
        run: |
          set -euo pipefail
          command_id="$(aws ssm send-command \
            --document-name "$DOCS_SSM_DOCUMENT_NAME" \
            --instance-ids "$DOCS_INSTANCE_ID" \
            --parameters "ReleaseSha=${{ steps.release.outputs.sha }}" \
            --query Command.CommandId --output text)"
          echo "id=$command_id" >> "$GITHUB_OUTPUT"
      - name: Wait for installer
        shell: bash
        run: |
          set -euo pipefail
          aws ssm wait command-executed \
            --command-id "${{ steps.command.outputs.id }}" \
            --instance-id "$DOCS_INSTANCE_ID"
          test "$(aws ssm get-command-invocation \
            --command-id "${{ steps.command.outputs.id }}" \
            --instance-id "$DOCS_INSTANCE_ID" \
            --query Status --output text)" = Success
      - name: Wait for healthy ALB target
        run: aws elbv2 wait target-in-service --target-group-arn "$DOCS_TARGET_GROUP_ARN"
      - name: Verify public and protected routes
        shell: bash
        run: |
          set -euo pipefail
          public_headers="$(mktemp)"
          curl --fail --silent --show-error --max-redirs 0 \
            --dump-header "$public_headers" --output /dev/null \
            "$DOCS_PUBLIC_BASE_URL/privacy-policy/"
          grep -qi '^content-type: text/html' "$public_headers"
          protected_headers="$(mktemp)"
          status="$(curl --silent --show-error --max-redirs 0 \
            --dump-header "$protected_headers" --output /dev/null \
            --write-out '%{http_code}' "$DOCS_PUBLIC_BASE_URL/guides/")"
          test "$status" = 307
          grep -qi '^location: /_auth/login' "$protected_headers"
```

- [ ] **Step 4: Run workflow and dependency-policy tests**

Run:

```bash
.venv/bin/python -m pytest \
  server/tests/test_deploy_workflow.py \
  server/tests/test_requirements.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/deploy.yml server/tests/test_deploy_workflow.py
git commit -m "LSA-10037 deploy immutable releases through SSM"
```

---

### Task 6: CI Verifies the Native Release Path

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `server/tests/test_requirements.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `scripts/build-release.sh` and the complete root/server lock files.
- Produces: CI evidence that the archive builds, installs offline, starts under
  Uvicorn, serves `/health`, and rejects encoded traversal before deployment.

- [ ] **Step 1: Write failing CI-policy tests**

Add to `server/tests/test_requirements.py`:

```python
def test_ci_exercises_the_native_release_without_production_docker() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "./scripts/build-release.sh" in workflow
    assert "--no-index" in workflow
    assert "dist/releases" in workflow
    assert "uvicorn server.main:app" in workflow
    assert "docker build --tag authifi-docs:test" not in workflow


def test_ci_keeps_optional_compose_mock_coverage_explicit() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "Credential-free local mock OIDC smoke test" in workflow
```

- [ ] **Step 2: Run the focused tests and confirm the red state**

Run:

```bash
.venv/bin/python -m pytest server/tests/test_requirements.py \
  -k "native_release or optional_compose" -v
```

Expected: FAIL because CI still treats the production container as its runtime artifact.

- [ ] **Step 3: Replace production container probes with release probes**

In `.github/workflows/ci.yml`, retain dependency installation, the full pytest
suite, strict MkDocs build, Terraform checks, and the credential-free local
mock OIDC smoke test. Replace Docker image build/rootless runtime steps with:

```yaml
- name: Build native release artifact
  run: ./scripts/build-release.sh "$GITHUB_SHA" dist/releases

- name: Verify offline release installation
  shell: bash
  run: |
    set -euo pipefail
    mkdir -p dist/expanded
    tar -xzf "dist/releases/$GITHUB_SHA.tar.gz" -C dist/expanded
    python -m venv dist/runtime
    dist/runtime/bin/pip install --no-index \
      --find-links dist/expanded/wheelhouse \
      -r dist/expanded/requirements.txt

- name: Probe native release server
  shell: bash
  run: |
    set -euo pipefail
    export OIDC_ISSUER=https://issuer.example.com
    export OIDC_CLIENT_ID=docs
    export SESSION_SECRET=ci-session-secret
    export PUBLIC_BASE_URL=http://127.0.0.1:8080
    export SITE_DIR="$PWD/dist/expanded/site"
    dist/runtime/bin/uvicorn server.main:app \
      --app-dir dist/expanded --host 127.0.0.1 --port 8080 \
      >dist/uvicorn.log 2>&1 &
    pid=$!
    trap 'kill "$pid" 2>/dev/null || true' EXIT
    for _ in $(seq 1 30); do
      curl --fail --silent http://127.0.0.1:8080/health && break
      sleep 1
    done
    curl --fail --silent http://127.0.0.1:8080/health
    test "$(curl --path-as-is --silent --output /dev/null --write-out '%{http_code}' \
      http://127.0.0.1:8080/assets/%2e%2e/index.html)" = 404
```

Do not add a `pip install` command for a requirements file that README does not
also install; the existing parity tests compare the reachable lock-file sets.

- [ ] **Step 4: Update README setup commands to preserve CI parity**

Keep the root and server lock installation commands explicit:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install -r server/requirements-dev.txt
```

Add the local release check:

```bash
make release
```

- [ ] **Step 5: Run CI-policy tests and a local release probe**

Run:

```bash
.venv/bin/python -m pytest server/tests/test_requirements.py -q
RELEASE_SHA="$(git rev-parse HEAD)" RELEASE_DIR=/tmp/authifi-docs-release make release
```

Expected: all tests pass and the archive/checksum exist under
`/tmp/authifi-docs-release`.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/ci.yml server/tests/test_requirements.py README.md
git commit -m "LSA-10037 verify the native EC2 release path"
```

---

### Task 7: Operations Documentation and Changeset

**Files:**
- Rewrite: `infra/README.md`
- Modify: `docs/operations/aws-oidc-hosting.md`
- Modify: `README.md`
- Modify: `CONTRIBUTING.md`
- Modify: `.changeset/aws-oidc-hosting.md`

**Interfaces:**
- Consumes: final Terraform outputs, repository variables, workflow names, and
  installer behavior from Tasks 1–6.
- Produces: one consistent operator path for bootstrap, Authifi registration,
  deployment, verification, rollback, and Cloudflare cutover.

- [ ] **Step 1: Write failing documentation drift tests**

Add architecture assertions to `server/tests/test_ec2_infra.py`:

```python
def test_operator_docs_name_the_native_ec2_architecture_only() -> None:
    paths = [
        ROOT / "README.md",
        ROOT / "infra" / "README.md",
        ROOT / "docs" / "operations" / "aws-oidc-hosting.md",
        ROOT / ".changeset" / "aws-oidc-hosting.md",
    ]
    text = "\n".join(path.read_text() for path in paths).lower()
    assert "application load balancer" in text
    assert "private ec2" in text
    assert "systemd" in text
    assert "aws app runner" not in text
    assert "amazon ecr" not in text


def test_docs_describe_public_pkce_registration_without_a_secret() -> None:
    text = (ROOT / "docs" / "operations" / "aws-oidc-hosting.md").read_text()
    assert "public client" in text
    assert "PKCE S256" in text
    assert "token_endpoint_auth_method=none" in text
```

- [ ] **Step 2: Run documentation tests and confirm the red state**

Run:

```bash
.venv/bin/python -m pytest server/tests/test_ec2_infra.py \
  -k "operator_docs or public_pkce" -v
```

Expected: FAIL on current App Runner/ECR and confidential-client language.

- [ ] **Step 3: Rewrite the infrastructure runbook**

Preserve these existing `infra/README.md` backend guarantees:

```bash
cat > infra/backend_override.tf <<'EOF'
terraform {
  backend "s3" {}
}
EOF
terraform -chdir=infra init \
  -backend-config="bucket=my-tf-state" \
  -backend-config="key=authifi/docs/prod.tfstate" \
  -backend-config="region=us-east-1"
```

Keep `Local state (the default)` before `Remote state supplied by the caller`,
and keep the runnable `check_terraform_backend` function unchanged so
`server/tests/test_infra.py` continues to execute it.

Replace image bootstrap with:

```bash
terraform -chdir=infra init
terraform -chdir=infra plan -var-file=terraform.tfvars
terraform -chdir=infra apply -var-file=terraform.tfvars

terraform -chdir=infra output -raw github_deploy_role_arn
terraform -chdir=infra output -raw release_bucket_name
terraform -chdir=infra output -raw instance_id
terraform -chdir=infra output -raw target_group_arn
terraform -chdir=infra output certificate_validation_records

# After publishing the validation records and ACM reports ISSUED:
terraform -chdir=infra apply -var-file=terraform.tfvars \
  -var='enable_https_listener=true'
```

Document repository variables with those exact output names, ACM DNS
validation, the required second apply, ALB alias/CNAME setup, first release,
SSM diagnostics, target health, and rollback by dispatching an earlier
40-character commit SHA.

- [ ] **Step 4: Align application and contributor docs**

Update `docs/operations/aws-oidc-hosting.md` runtime, health, canonical-host,
production registration, deployment, cutover, and rollback sections. Preserve
the architecture-independent authorization, path, session, logout, and local
mock sections.

The production registration must say:

```text
Client type: public
Grant: authorization code
PKCE: required, S256
Token endpoint authentication method: none
Callback: https://docs.authifi.io/_auth/callback
Post-logout redirect: https://docs.authifi.io/privacy-policy/
Scopes: openid profile email
```

Update `README.md` and `CONTRIBUTING.md` to describe native production releases
and optional local Docker Compose. Remove the stale references to
`overrides/partials/header-public.html` and
`test_public_header_is_the_material_header_minus_search`.

Change `.changeset/aws-oidc-hosting.md` to:

```markdown
---
"@authifi/docs": major
---

Move the documentation site to an OIDC-protected application on a private EC2
instance behind an AWS Application Load Balancer, deployed through S3 and SSM.
```

- [ ] **Step 5: Run documentation, backend, and build checks**

Run:

```bash
.venv/bin/python -m pytest \
  server/tests/test_ec2_infra.py \
  server/tests/test_infra.py \
  server/tests/test_requirements.py \
  server/tests/test_public_boundary.py -q
.venv/bin/python -m mkdocs build --strict
```

Expected: all tests pass and MkDocs completes without warnings.

- [ ] **Step 6: Commit**

```bash
git add infra/README.md docs/operations/aws-oidc-hosting.md README.md \
  CONTRIBUTING.md .changeset/aws-oidc-hosting.md \
  server/tests/test_ec2_infra.py
git commit -m "LSA-10037 document native EC2 operations"
```

---

### Task 8: Full Verification and Review Gate

**Files:**
- Inspect: all changed files
- Modify only if verification exposes a defect.

**Interfaces:**
- Consumes: completed Tasks 1–7.
- Produces: merge-ready branch evidence without deploying or mutating AWS.

- [ ] **Step 1: Run the complete Python suite**

Run:

```bash
.venv/bin/python -m pytest server/tests -q
```

Expected: all tests pass.

- [ ] **Step 2: Run strict site and release builds**

Run:

```bash
rm -rf site dist/verification
.venv/bin/python -m mkdocs build --strict
./scripts/build-release.sh "$(git rev-parse HEAD)" dist/verification
sha256sum --check "dist/verification/$(git rev-parse HEAD).tar.gz.sha256"
```

Expected: strict build succeeds and checksum reports `OK`.

- [ ] **Step 3: Run static configuration checks**

Run:

```bash
terraform -chdir=infra fmt -check -recursive
terraform -chdir=infra init -backend=false
terraform -chdir=infra validate
docker compose -f compose.yaml -f compose.mock.yaml config --quiet
docker compose -f compose.yaml -f compose.real.yaml config --quiet
shellcheck scripts/build-release.sh infra/scripts/deploy-release.sh
git diff --check origin/main...HEAD
```

Expected: every command exits 0.

- [ ] **Step 4: Run the credential-free local OIDC smoke test**

Run:

```bash
make local-smoke
```

Expected: login, callback, protected content, logout, public content, and
traversal probes all pass.

- [ ] **Step 5: Review the branch and resolve significant findings**

Run the repository's requested local code-review process against
`origin/main...HEAD`. For each significant finding:

1. add a regression test that fails;
2. implement the smallest fix;
3. rerun the focused test;
4. rerun the complete verification steps affected by the fix;
5. commit with an `LSA-10037` message.

- [ ] **Step 6: Confirm branch and PR metadata**

Run:

```bash
git status --short
git log --oneline origin/main..HEAD
gh pr view 49 --json url,title,headRefName,statusCheckRollup
```

Expected: clean worktree, every new commit names `LSA-10037`, PR 49 points at
`LSA-10037/aws-oidc`, and required checks are green before handoff.
