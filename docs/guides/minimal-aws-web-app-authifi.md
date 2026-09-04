# Deploy a Minimal AWS Web App with Authifi

Start with managed hosting. For most small applications, you should not need
to design a VPC, provision an EC2 host, configure an ALB, issue a certificate,
or write a deployment script before testing login.

| Application | AWS service | Authifi client |
| --- | --- | --- |
| Browser-only SPA with a separate API | [Amplify Hosting](https://docs.aws.amazon.com/amplify/latest/userguide/welcome.html) | Public client with PKCE |
| Server-rendered app or BFF | [ECS Express Mode](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/express-service-overview.html) | Confidential client |
| Host-level control or an existing private network | EC2 and ALB | Confidential client |

Use the [React SPA starter](https://github.com/Authifi/docs/tree/main/examples/aws-authifi-starters/react-amplify-spa) for the first path and the [Next.js ECS Express starter](https://github.com/Authifi/docs/tree/main/examples/aws-authifi-starters/nextjs-ecs-express) for the second. The EC2 path is at the end of this page for teams that need it.

!!! note
    AWS App Runner is no longer open to new customers as of March 31, 2026. Existing customers can continue using it, but new deployments should use ECS Express Mode instead. See the [App Runner `CreateService` API note](https://docs.aws.amazon.com/apprunner/latest/api/API_CreateService.html).

## Before you start

You need:

- an Authifi tenant administrator;
- an AWS account with permission to create the selected hosting resources.

For the first deployment, use the AWS-managed URL that Amplify or ECS Express
provides. Add a custom domain after the application works. Amplify and ECS
Express can provide HTTPS for a custom domain; see [Amplify custom domains](https://docs.aws.amazon.com/amplify/latest/userguide/custom-domains.html) and [ECS Express custom domains](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/express-service-advanced-customization.html).

The ECS Express starter also needs Node.js 22+, Docker, AWS CLI credentials,
and a default VPC in the target Region. Amplify only needs a Git repository and
the build settings in the starter.

## Register the Authifi client

Find the tenant issuer first:

```text
<AUTHIFI_HOST>/_api/auth/<TENANT>
```

The issuer is the tenant URL above. It is not the documentation domain and it
does not have an `/oidc` suffix.

The discovery document is `<AUTHIFI_HOST>/_api/auth/<TENANT>/.well-known/openid-configuration`.
The token endpoint is the exception: it ends in `/oidc/token`.

Register the client in **SSO Integration → App Dashboard** after you know the
hosting URL. Use the exact URL; redirect URI matching is case-sensitive.

| Setting | Amplify SPA | ECS Express BFF |
| --- | --- | --- |
| Client type | Public | Confidential web |
| Callback | `https://<APP_URL>/callback` | `https://<APP_URL>/api/auth/callback/authifi` |
| Post-logout URL | `https://<APP_URL>/` | `https://<APP_URL>/` |
| Grant and response | `authorization_code`, `code` | `authorization_code`, `code` |
| Scopes | `openid profile email offline_access` | `openid profile email offline_access` |
| Secret | None | Keep only in AWS Secrets Manager |

Allow only the identity providers and groups that should access the application.

If the application calls a protected API, create or select its resource server
identifier. Send `resource=<RESOURCE_SERVER_IDENTIFIER>` on both the authorize
request and the token request. APIs authorize the resulting space-separated
`scope` claim; do not use a client secret in browser code.

Keep these non-secret values:

```dotenv
AUTHIFI_HOST=https://auth.example.com
AUTHIFI_TENANT=your-tenant
AUTHIFI_CLIENT_ID=replace-with-client-id
AUTHIFI_RESOURCE=https://app.example.com/api
```

See [SSO Integration](sso-integration-guide.md) for provider, group, role, and
permission configuration.

## Path 1: React SPA with Amplify Hosting

Amplify builds a repository branch and publishes the output to its managed CDN.
The included configuration also rewrites browser routes to `index.html`, so a
refresh on `/callback` works without a server.

### Deploy the starter

1. Copy `examples/aws-authifi-starters/react-amplify-spa` into a GitHub repository.
2. In **AWS Amplify → New app → Host web app**, connect the repository and select its branch.
3. Keep the checked-in `amplify.yml` build settings.
4. Start the deployment and copy the generated `https://...amplifyapp.com` URL.

The starter uses `oidc-client-ts` with authorization code flow and S256 PKCE.
It has no server and does not need a client secret.

### Configure Authifi and redeploy

Create a public Authifi client with:

```text
Callback:          https://<AMPLIFY_URL>/callback
Post-logout URL:   https://<AMPLIFY_URL>/
```

In the Amplify application settings, add these build-time environment variables:

```text
VITE_AUTHIFI_HOST
VITE_AUTHIFI_TENANT
VITE_AUTHIFI_CLIENT_ID
VITE_AUTHIFI_RESOURCE   # only when calling an API
```

Redeploy the branch. Build-time variables are visible to browser users, so they
may contain the issuer, tenant, client ID, and resource identifier only.

To use a custom domain, open **Hosting → Custom domains** and follow [AWS's
Amplify instructions](https://docs.aws.amazon.com/amplify/latest/userguide/custom-domains.html).
Update the Authifi callback and post-logout URLs when the public URL changes.

### Protect an API

The browser sends the access token as:

```http
Authorization: Bearer <access-token>
```

The API must validate the JWT signature, exact issuer, audience, expiry, and
required permission scopes. Return `401` for a missing or invalid token and
`403` for a valid token without the required permission. Use the
[Authifi API JWT guidance](https://github.com/Authifi/idbroker/tree/main/skills/authifi/api-jwt)
for the resource-server implementation.

## Path 2: Next.js BFF with ECS Express Mode

ECS Express Mode runs a container on Fargate and provisions the supporting
load balancer, HTTPS endpoint, security groups, health check, logging, and
scaling defaults. It requires a container image plus an ECS task execution role
and infrastructure role, but the starter creates those pieces with CDK. See
[AWS's ECS Express creation guide](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/express-service-create-full.html).

The starter keeps the Authifi token exchange on the server. It creates an ECR
image asset, two Secrets Manager secrets, the required IAM roles, and an
`AWS::ECS::ExpressGatewayService` resource. It does not require a hand-built
VPC, NAT gateway, EC2 host, or ALB.

### Deploy the starter

From `examples/aws-authifi-starters/nextjs-ecs-express`:

```bash
npm install
npx cdk bootstrap
npm run deploy
```

Before the first deployment, set the non-secret values in `cdk.json`:

```json
{
  "context": {
    "authifiHost": "https://auth.example.com",
    "authifiTenant": "your-tenant",
    "authifiClientId": "pending",
    "authifiResource": "",
    "appUrl": "",
    "deploymentVersion": "1"
  }
}
```

The first deployment outputs `AppUrl`, `ServiceArn`, and
`AuthifiClientSecretArn`. Copy `AppUrl` and register a confidential Authifi
client with:

```text
Callback:          https://<ECS_EXPRESS_URL>/api/auth/callback/authifi
Post-logout URL:   https://<ECS_EXPRESS_URL>/
```

Put the returned client ID in `cdk.json`. Store the returned client secret in
the Secrets Manager secret named by `AuthifiClientSecretArn`:

```bash
read -rsp "Authifi client secret: " AUTHIFI_CLIENT_SECRET
printf '\n'
aws secretsmanager put-secret-value \
  --secret-id <AuthifiClientSecretArn> \
  --secret-string "$AUTHIFI_CLIENT_SECRET"
unset AUTHIFI_CLIENT_SECRET
```

Put the `AppUrl` value in `cdk.json` as `appUrl`, then run `npm run deploy`
again. This second deployment updates the service with the real client ID and
canonical application URL.

The container receives Secrets Manager values when a task starts. After later
secret rotation, increment `deploymentVersion` in `cdk.json` and run
`npm run deploy` to start a new service revision.

To add a custom domain, follow [AWS's ECS Express custom-domain guidance](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/express-service-advanced-customization.html),
then update `appUrl` and the Authifi callback and post-logout URLs.

For an application that must reach private AWS resources, provide the required
subnets and security groups in the stack's `networkConfiguration`. The default
starter uses the default VPC and public subnets so it can reach public Authifi
OIDC endpoints without a NAT gateway.

## Verify the deployment

Run these checks for either path:

1. Visit the public landing page.
2. An unauthenticated visit to protected content redirects to the expected Authifi issuer.
3. Authifi returns to the exact registered callback and the application establishes a session or completes the SPA callback.
4. Logout clears local state and returns to the registered post-logout URL.
5. If an API is present, a missing or invalid token returns `401`, a valid token with insufficient permission returns `403`, and an authorized request succeeds.

For the ECS Express starter, also check `GET /api/health` returns `200`.

Confirm that no client secret, access token, refresh token, or session secret
appears in browser bundles, source control, or application logs.

## When to use EC2 instead

Use the EC2/ALB design only when you need host-level control, an existing
private-subnet deployment convention, custom operating-system packages, or a
deployment system that the managed services cannot run.

That design requires an internet-facing ALB with an ACM certificate, an EC2
instance without a public IP, security groups that allow only ALB-to-instance
traffic, an egress path from the private subnet to Authifi, an instance role,
and a host deployment mechanism. Systems Manager Run Command can replace SSH;
it does not replace the network route. Reuse existing organizational
infrastructure where possible and use [AWS's EC2 web-server guidance](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-instance-launch.html)
for the host-specific work.

For a highly available or multi-service production system, use the
organization's existing ECS, EKS, or platform deployment standard instead of
turning this minimal example into a second infrastructure platform.

## References

- [Authifi SSO Integration](sso-integration-guide.md)
- [Authifi OIDC request scopes](oidc-request-scopes.md)
- [AWS Amplify Hosting](https://docs.aws.amazon.com/amplify/latest/userguide/welcome.html)
- [Amazon ECS Express Mode](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/express-service-overview.html)
- [ECS Express resources and defaults](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/express-service-work.html)
- [ECS Express service creation](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/express-service-create-full.html)
