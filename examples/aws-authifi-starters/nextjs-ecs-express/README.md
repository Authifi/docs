# Next.js BFF on ECS Express Mode

This starter uses NextAuth with a server-held Authifi confidential client. The
browser receives an HTTP-only session cookie; the client secret and session
secret are stored in AWS Secrets Manager.

## Local development

```bash
cp .env.example .env.local
npm install
npm run dev
```

Register this callback in Authifi:

```text
http://localhost:3000/api/auth/callback/authifi
```

## ECS Express deployment

Requirements: Node.js 22+, Docker, AWS CLI credentials, an Authifi host and
tenant, and a default VPC in the target Region.

1. Set `authifiHost`, `authifiTenant`, and `authifiClientId` in `cdk.json`. Leave
   the client ID as `pending` for the first deployment.
2. Bootstrap CDK once in the target AWS account and Region:

   ```bash
   npx cdk bootstrap
   ```

3. Build the image, create the ECS Express service, and print its outputs:

   ```bash
   npm run deploy
   ```

4. Copy `AppUrl` from the output and register a confidential Authifi client:

   ```text
   https://<ECS_EXPRESS_URL>/api/auth/callback/authifi
   ```

5. Put the returned client ID in `cdk.json`. Put the one-time client secret in
   the Secrets Manager secret named by `AuthifiClientSecretArn`:

   ```bash
   read -rsp "Authifi client secret: " AUTHIFI_CLIENT_SECRET
   printf '\n'
   aws secretsmanager put-secret-value \
     --secret-id <AuthifiClientSecretArn> \
     --secret-string "$AUTHIFI_CLIENT_SECRET"
   unset AUTHIFI_CLIENT_SECRET
   ```

6. Put the `AppUrl` value in `cdk.json` as `appUrl`, then run the deployment
   again:

   ```bash
   npm run deploy
   ```

The stack creates the ECR image asset, two Secrets Manager secrets, the ECS
task execution and infrastructure roles, and the ECS Express service. ECS
Express Mode creates the load balancer, HTTPS endpoint, health check, logs,
security groups, and default scaling configuration. It uses the default VPC
unless you add a custom network configuration to the stack.

The `AppUrl` output is the managed HTTPS endpoint. Add a custom domain after it
works by following [AWS's ECS Express custom-domain instructions](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/express-service-advanced-customization.html),
then update `appUrl` and the Authifi callback and post-logout URLs.

The container receives Secrets Manager values when a task starts. After
rotating a secret, increment `deploymentVersion` in `cdk.json` and run
`npm run deploy` to start a new service revision.

## Routes

- `/` — public landing page;
- `/login` — starts the Authifi redirect;
- `/dashboard` — server-protected page; and
- `/api/health` — unauthenticated ECS Express health check.

## References

- [Amazon ECS Express Mode](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/express-service-overview.html)
- [Create an ECS Express service](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/express-service-create-full.html)
- [ECS Express resources and defaults](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/express-service-work.html)
