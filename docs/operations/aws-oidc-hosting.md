# AWS OIDC Hosting Notes

This note supplements the repository's [`infra/README.md`](https://github.com/axleresearch/authifi-docs/blob/main/infra/README.md) with repo-specific operating guidance for the Authifi docs site's AWS and OIDC hosting model. Use that file for the canonical Terraform bootstrap commands.

## Runtime Overview

- MkDocs produces the static site in `site/`.
- The Starlette server in `server/` serves that build, keeps specific legal and discovery paths public, and redirects protected content through Authifi OIDC.
- The production container image is built from `Dockerfile`, pushed to ECR, and run on AWS App Runner.

## Local Mock Networking

`make local-mock-up` relies on the `compose.mock.yaml` `extra_hosts` entry that maps the configured mock issuer hostname to Docker's host gateway.

This works on:

- Docker Desktop
- standard rootful Linux Docker engines that support `host-gateway`

Rootless Linux engines may require an override, such as:

- using a different reachable hostname or IP for `MOCK_OIDC_HOST`
- removing the `host-gateway` mapping in a local compose override
- publishing the mock issuer under a host name your engine can already resolve from containers

If the docs container cannot resolve the mock issuer host, the login flow will fail before callback handling.

## Production OIDC Registration

The Authifi application for this docs host should be a confidential Web App with:

- callback URL: `https://docs.authifi.io/_auth/callback`
- logout or post-logout return URL: `https://docs.authifi.io/`
- scopes: `openid profile email`

For local real-OIDC work, also register `http://localhost:8000/_auth/callback` and `http://localhost:8000/`, or the equivalent base URL you actually use.

## Deployment Checklist

1. Bootstrap infra per the repository's [`infra/README.md`](https://github.com/axleresearch/authifi-docs/blob/main/infra/README.md).
2. Confirm GitHub repository variables match Terraform outputs:
   - `AWS_REGION`
   - `AWS_DEPLOY_ROLE_ARN`
   - `APP_RUNNER_SERVICE_ARN`
   - `ECR_REPOSITORY_URL`
3. Merge to `main` and monitor `.github/workflows/deploy.yml`.
4. After the App Runner custom domain association is ready, publish the required DNS records.
5. Verify both public and protected route behavior before announcing success.

## Verification Targets

After a deploy or cutover, verify:

- `https://docs.authifi.io/` redirects to `/_auth/login` when unauthenticated
- `https://docs.authifi.io/privacy-policy/` returns `200`
- `https://docs.authifi.io/terms-of-service/` returns `200`
- `https://docs.authifi.io/sms-opt-in.html` returns `200`
- `https://docs.authifi.io/robots.txt` returns `200`
- `https://docs.authifi.io/auth.md` returns `200`
- `https://docs.authifi.io/sitemap.xml` contains only public legal URLs
- protected guides and security pages are absent from `sitemap.xml`

## Rollback Options

Choose the smallest rollback that fixes the issue:

- DNS rollback: repoint `docs.authifi.io` to the previous target when the issue is at the custom-domain or edge layer
- image rollback: redeploy App Runner with a previously known-good immutable image tag when the issue is in the built site or server

Use the Terraform and App Runner procedures in the repository's [`infra/README.md`](https://github.com/axleresearch/authifi-docs/blob/main/infra/README.md) for the exact commands.
