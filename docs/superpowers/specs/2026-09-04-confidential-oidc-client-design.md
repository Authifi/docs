# Confidential Authifi Client Design

## Goal

Change the production docs host from a public OIDC client to the confidential
client Authifi recommends for a server-side application, without adding another
operator-managed AWS setting.

## Authentication

The authorization flow remains Authorization Code with PKCE S256. When a client
secret is present, Authlib will authenticate the token request with
`client_secret_post`; the secret is sent only to Authifi's HTTPS token endpoint.
Local development may continue to provide `OIDC_CLIENT_SECRET` directly.

## Secret ownership and delivery

The `production` GitHub Environment is the operator-facing source of truth for
`OIDC_CLIENT_SECRET`. Before invoking the existing SSM deployment document, the
deployment workflow writes that value to a fixed SSM Parameter Store
`SecureString`. The workflow must fail before deployment when the GitHub secret
is absent.

The secret itself is never passed as an SSM command parameter and never enters
Terraform state, a release archive, user data, or workflow output. Terraform
passes only the fixed parameter name to the instance and grants:

- the GitHub deployment role `ssm:PutParameter` for that exact parameter; and
- the EC2 instance role `ssm:GetParameter` for that exact parameter.

The app resolves the parameter with decryption through the instance role when
it starts and keeps the value only in process memory. The first deployment
creates the parameter before starting the first release. Updating the Authifi
secret means updating the GitHub Environment secret and deploying again; the
normal service restart loads the new value.

Parameter Store is already part of the Systems Manager service used by this
deployment. Operators do not create or edit this parameter directly in AWS.
Teardown documentation will include deleting the workflow-managed parameter,
because Terraform cannot own its secret value without recording it in state.

## Failure behavior

Startup fails without exposing the secret when the parameter is missing,
unreadable, or empty. A failed candidate startup is handled by the existing
deployment rollback path. The workflow must not print the secret or place it in
command arguments that AWS records.

## Focused verification

The change adds only tests that protect the new boundary:

1. a configured secret selects `client_secret_post`;
2. production configuration resolves the named SecureString without logging or
   persisting its value;
3. Terraform grants exact-parameter permissions and stores only its name; and
4. the deployment workflow requires and synchronizes the GitHub Environment
   secret before invoking SSM.

Existing OIDC, infrastructure, deployment, and release tests remain the
regression suite. No additional review cycle is planned beyond one focused
review for release-blocking findings.
