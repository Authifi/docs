# Confidential Authifi Client Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the production server an Authifi confidential client using PKCE S256 and `client_secret_post`, with GitHub as the only operator-managed secret surface.

**Architecture:** The production workflow copies its `OIDC_CLIENT_SECRET` environment secret into the fixed `/authifi-docs/oidc-client-secret` SSM SecureString before invoking the existing deployment document. Terraform grants exact-parameter access and passes only the parameter name to EC2; the Python process retrieves and decrypts the value through its instance role at startup.

**Tech Stack:** Python 3.12, Authlib, boto3, Terraform, GitHub Actions, AWS Systems Manager Parameter Store

## Global Constraints

- Keep Authorization Code with PKCE S256.
- Use `client_secret_post`, not HTTP Basic, for a configured confidential client.
- Never put the secret in Terraform state, EC2 user data, release artifacts, SSM command parameters, or logs.
- Keep `/authifi-docs/oidc-client-secret` fixed so operators configure no additional repository variable.
- Use the existing AWS-managed Parameter Store key; do not add a customer-managed KMS key.
- Add only the four boundary tests named in the approved design and run one focused review.

---

### Task 1: Resolve the confidential-client secret at process startup

**Files:**
- Modify: `server/app.py`
- Modify: `server/requirements.in`
- Regenerate: `server/requirements.txt`
- Test: `server/tests/test_app.py`
- Test: `server/tests/test_requirements.py`

**Interfaces:**
- Consumes: `OIDC_CLIENT_SECRET` for local use or `OIDC_CLIENT_SECRET_PARAMETER_NAME` in production.
- Produces: `resolve_oidc_client_secret(environ, parameter_loader)` returning `str | None`; `AppConfig.from_env(..., parameter_loader=...)`; Authlib client metadata with `token_endpoint_auth_method="client_secret_post"`.

- [ ] **Step 1: Write failing runtime tests**

Add focused tests that inject a loader instead of contacting AWS:

```python
def test_app_config_resolves_named_oidc_secret(site_dir: Path) -> None:
    requested: list[str] = []
    values = {
        "OIDC_ISSUER": "https://issuer.example.com",
        "OIDC_CLIENT_ID": "docs",
        "OIDC_CLIENT_SECRET_PARAMETER_NAME": "/authifi-docs/oidc-client-secret",
        "SESSION_SECRET": "session-secret",
        "PUBLIC_BASE_URL": "https://docs.example.com",
        "SITE_DIR": str(site_dir),
    }

    config = AppConfig.from_env(
        values,
        parameter_loader=lambda name: requested.append(name) or "resolved-secret",
    )

    assert requested == ["/authifi-docs/oidc-client-secret"]
    assert config.oidc_client_secret == "resolved-secret"
```

Change the existing token-authentication assertion so a configured secret expects
`client_secret_post`. Add one refusal test for an empty resolved value; assert
only the parameter name appears in the exception, never the loader's returned
value.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
python -m pytest \
  server/tests/test_app.py::test_app_config_resolves_named_oidc_secret \
  server/tests/test_app.py::test_auth_client_selects_token_authentication_from_secret_presence \
  -q
```

Expected: FAIL because `from_env` has no `parameter_loader` parameter and the
configured client still selects `client_secret_basic`.

- [ ] **Step 3: Implement secret resolution and POST authentication**

Add a small default loader and keep it injectable:

```python
SecretParameterLoader = Callable[[str], str]


def load_ssm_secure_string(name: str) -> str:
    import boto3

    response = boto3.client("ssm").get_parameter(Name=name, WithDecryption=True)
    value = response["Parameter"]["Value"]
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"OIDC client secret parameter {name!r} is empty")
    return value


def resolve_oidc_client_secret(
    environ: Mapping[str, str],
    parameter_loader: SecretParameterLoader = load_ssm_secure_string,
) -> str | None:
    direct = environ.get("OIDC_CLIENT_SECRET") or None
    parameter_name = environ.get("OIDC_CLIENT_SECRET_PARAMETER_NAME") or None
    if direct and parameter_name:
        raise RuntimeError(
            "set only one of OIDC_CLIENT_SECRET and "
            "OIDC_CLIENT_SECRET_PARAMETER_NAME"
        )
    if not parameter_name:
        return direct
    value = parameter_loader(parameter_name)
    if not value:
        raise RuntimeError(f"OIDC client secret parameter {parameter_name!r} is empty")
    return value
```

Extend `AppConfig.from_env` with the injected loader, call
`resolve_oidc_client_secret`, and change the configured method:

```python
token_auth_method = (
    "client_secret_post" if config.oidc_client_secret else "none"
)
```

Add `boto3` at an exact version to `server/requirements.in`.

- [ ] **Step 4: Regenerate and verify the dependency lock**

Run the exact Docker command in the header of `server/requirements.txt`, redirect
its package output into a temporary file, and update the generated lock while
preserving its explanatory header and `via` annotations. Then run:

```bash
python -m pytest server/tests/test_app.py server/tests/test_requirements.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit the runtime boundary**

```bash
git add server/app.py server/requirements.in server/requirements.txt \
  server/tests/test_app.py server/tests/test_requirements.py
git commit -m "LSA-10037 use confidential Authifi client authentication"
```

---

### Task 2: Synchronize the GitHub secret and grant exact AWS access

**Files:**
- Modify: `infra/main.tf`
- Modify: `infra/scripts/deploy-release.sh`
- Modify: `.github/workflows/deploy.yml`
- Test: `server/tests/test_ec2_infra.py`
- Test: `server/tests/test_deploy_workflow.py`
- Test: `server/tests/test_deploy_release.py`

**Interfaces:**
- Consumes: GitHub production-environment secret `OIDC_CLIENT_SECRET`.
- Produces: fixed SecureString `/authifi-docs/oidc-client-secret`; host setting `OIDC_CLIENT_SECRET_PARAMETER_NAME`; exact `ssm:PutParameter` and `ssm:GetParameter` IAM grants.

- [ ] **Step 1: Write failing infrastructure and workflow tests**

Add assertions that:

```python
assert 'OIDC_CLIENT_SECRET_PARAMETER_NAME = "/authifi-docs/oidc-client-secret"' in main_tf
assert "ssm:GetParameter" in main_tf
assert "ssm:PutParameter" in main_tf
assert '${{ secrets.OIDC_CLIENT_SECRET }}' in deploy_workflow
assert "aws ssm put-parameter" in deploy_workflow
assert deploy_workflow.index("aws ssm put-parameter") < deploy_workflow.index(
    "aws ssm send-command"
)
```

Parse the Terraform IAM policy JSON in the existing infrastructure test style
and require each action to target only the exact parameter ARN. Extend the
installer's accepted host-configuration fixture with
`OIDC_CLIENT_SECRET_PARAMETER_NAME`; keep the tests that reject
`OIDC_CLIENT_SECRET` itself.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
python -m pytest \
  server/tests/test_ec2_infra.py \
  server/tests/test_deploy_workflow.py \
  server/tests/test_deploy_release.py \
  -q
```

Expected: FAIL because the fixed parameter name, IAM grants, workflow secret,
and sixth host-configuration key do not exist.

- [ ] **Step 3: Add the fixed parameter identity and least-privilege policies**

In `infra/main.tf`, define:

```hcl
oidc_client_secret_parameter_name = "/authifi-docs/oidc-client-secret"
oidc_client_secret_parameter_arn  = "arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter${local.oidc_client_secret_parameter_name}"
```

Add the name to `local.host_config`. Add `ssm:GetParameter` on the exact ARN to
the instance policy and `ssm:PutParameter` on the same ARN to the GitHub deploy
policy. Do not add `kms:*`: the AWS-managed `alias/aws/ssm` key is used.

Update `infra/scripts/deploy-release.sh` so its exact configuration allowlist
requires `OIDC_CLIENT_SECRET_PARAMETER_NAME` and passes that name, never a secret
value, to candidate processes.

- [ ] **Step 4: Synchronize the environment secret before deployment**

Give only the synchronization step access to the GitHub secret:

```yaml
- name: Synchronize OIDC client secret
  shell: bash
  env:
    OIDC_CLIENT_SECRET: ${{ secrets.OIDC_CLIENT_SECRET }}
  run: |
    set -euo pipefail
    : "${OIDC_CLIENT_SECRET:?Set production environment secret OIDC_CLIENT_SECRET}"
    aws ssm put-parameter \
      --name "/authifi-docs/oidc-client-secret" \
      --type SecureString \
      --value "$OIDC_CLIENT_SECRET" \
      --overwrite >/dev/null
```

Place it after AWS credential configuration and before release selection. Keep
the secret out of global `env`, SSM `send-command --parameters`, and shell
tracing.

- [ ] **Step 5: Run focused infrastructure verification**

Run:

```bash
terraform -chdir=infra fmt -check
terraform -chdir=infra init -backend=false
terraform -chdir=infra validate
python -m pytest \
  server/tests/test_ec2_infra.py \
  server/tests/test_deploy_workflow.py \
  server/tests/test_deploy_release.py \
  -q
```

Expected: all commands PASS.

- [ ] **Step 6: Commit delivery changes**

```bash
git add infra/main.tf infra/scripts/deploy-release.sh \
  .github/workflows/deploy.yml server/tests/test_ec2_infra.py \
  server/tests/test_deploy_workflow.py server/tests/test_deploy_release.py
git commit -m "LSA-10037 deliver OIDC client secret through SSM"
```

---

### Task 3: Add the logged-off landing page

**Files:**
- Create: `docs/logged-off.md`
- Modify: `docs/hooks/agent_assets.py`
- Modify: `server/app.py`
- Modify: `infra/variables.tf`
- Test: `server/tests/test_app.py`
- Test: `server/tests/test_public_boundary.py`
- Test: `server/tests/test_ec2_infra.py`

**Interfaces:**
- Consumes: `/_auth/login` and the existing public-page chrome suppression.
- Produces: public `https://docs.authifi.io/logged-off` and default
  `POST_LOGOUT_PATH=/logged-off`.

- [ ] **Step 1: Write failing page and redirect tests**

Add `/logged-off` to the exact public path contract and assert that an anonymous
request returns `200`, contains the logged-off heading, and links to
`/_auth/login` without first returning `308`. Add the generated
`logged-off/index.html` page to `PUBLIC_PAGE_URLS` so existing tests verify that
it leaks no protected navigation or search. Change default post-logout
assertions from `/privacy-policy/` to `/logged-off`.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest \
  server/tests/test_app.py \
  server/tests/test_public_boundary.py \
  server/tests/test_ec2_infra.py \
  -q
```

Expected: FAIL because the page, public route, direct extensionless mapping, and
new defaults do not exist.

- [ ] **Step 3: Add the public page and exact path mapping**

Create:

```markdown
---
title: Logged off
---

# You’ve been logged off

Your Authifi documentation session has ended.

[Sign in to Authifi docs](/_auth/login)
```

Add `logged-off.md` to `PUBLIC_PAGE_SOURCES`. Add both `/logged-off` and
`/logged-off/` to `PUBLIC_EXACT_PATHS`, set
`DEFAULT_POST_LOGOUT_PATH = "/logged-off"`, and map the extensionless path to
`logged-off/index.html` before directory canonicalization. Change Terraform's
`post_logout_path` default and accepted public-page list to `/logged-off`.

- [ ] **Step 4: Verify the page boundary**

Run:

```bash
.venv/bin/python -m pytest \
  server/tests/test_app.py \
  server/tests/test_public_boundary.py \
  server/tests/test_ec2_infra.py \
  -q
```

Expected: PASS.

- [ ] **Step 5: Commit the landing page**

```bash
git add docs/logged-off.md docs/hooks/agent_assets.py server/app.py \
  infra/variables.tf server/tests/test_app.py \
  server/tests/test_public_boundary.py server/tests/test_ec2_infra.py
git commit -m "LSA-10037 add logged-off landing page"
```

---

### Task 4: Update the operator contract and complete PR 53

**Files:**
- Modify: `README.md`
- Modify: `infra/README.md`
- Modify: `operations/aws-oidc-hosting.md`
- Modify: `server/tests/test_ec2_infra.py`

**Interfaces:**
- Consumes: the runtime and deployment behavior from Tasks 1 and 2.
- Produces: one operator action—set `OIDC_CLIENT_SECRET` in the GitHub
  `production` Environment—and a teardown command for the workflow-managed
  parameter.

- [ ] **Step 1: Write the failing documentation contract assertion**

Replace the existing public-client assertions with:

```python
assert "confidential client" in OPERATIONS_DOC
assert "PKCE S256" in OPERATIONS_DOC
assert "client_secret_post" in OPERATIONS_DOC
assert "OIDC_CLIENT_SECRET" in INFRA_README
```

- [ ] **Step 2: Run the assertion and verify RED**

Run:

```bash
python -m pytest server/tests/test_ec2_infra.py -q
```

Expected: FAIL because the documents still prescribe a public client with no
secret.

- [ ] **Step 3: Update the operator documentation**

Document:

- confidential Authifi registration with Authorization Code, PKCE S256, and
  `client_secret_post`;
- callback `https://docs.authifi.io/_auth/callback` and post-logout redirect
  `https://docs.authifi.io/logged-off`;
- creation of the GitHub `production` Environment secret
  `OIDC_CLIENT_SECRET`;
- automatic workflow synchronization to Parameter Store;
- rotation by updating Authifi, updating the GitHub secret, and deploying; and
- teardown with:

```bash
aws ssm delete-parameter \
  --region us-east-1 \
  --name /authifi-docs/oidc-client-secret
```

Remove statements that production intentionally has no client secret.

- [ ] **Step 4: Run the proportional regression suite**

Run:

```bash
python -m pytest server/tests -q
terraform -chdir=infra fmt -check
terraform -chdir=infra validate
git diff --check
```

Expected: all tests and validation PASS with no whitespace errors.

- [ ] **Step 5: Commit documentation**

```bash
git add README.md infra/README.md operations/aws-oidc-hosting.md \
  server/tests/test_ec2_infra.py
git commit -m "LSA-10037 document confidential Authifi deployment"
```

- [ ] **Step 6: Run one focused review and update PR 53**

Review only the changed runtime secret boundary, exact IAM resources, workflow
secret handling, and operator instructions. Fix release blockers, rerun the
focused tests, push `LSA-10037/aws-oidc`, and confirm PR 53 reflects the new
commits. Do not start another review cycle for non-blocking hardening.
