# AWS OIDC Hosting Notes

This note supplements the repository's [`infra/README.md`](https://github.com/Authifi/docs/blob/main/infra/README.md) with repo-specific operating guidance for the Authifi docs site's AWS and OIDC hosting model. Use that file for the canonical Terraform bootstrap commands.

## Runtime Overview

- MkDocs produces the static site in `site/`.
- The Starlette server in `server/` serves that build, keeps specific legal and discovery paths public, and redirects protected content through Authifi OIDC.
- The production container image is built from `Dockerfile`, pushed to ECR, and run on AWS App Runner.

## Authorization Policy

Authorization in v1 is **authentication only**: any identity that the configured Authifi tenant accepts through the OIDC flow may read every protected page. The server stores the subject, and optionally the email and name, and grants access on that basis alone.

There is deliberately **no group, role, or email-domain filtering in v1**. Controlling who can read the docs is therefore controlled entirely by controlling who can sign in to the configured tenant and who is assigned the docs application. If finer-grained access becomes a requirement, it is a follow-up change to the callback handler and the session contents, not a configuration toggle.

The boundary also hides the shape of the protected tree. Authorization runs before trailing-slash canonicalisation for protected routes, so an anonymous request for `/guides/sso-integration-guide` and one for a page that does not exist both answer `307` to `/_auth/login`, with `next` echoing the path exactly as requested. Signed-in callers get the usual `308` to the canonical form, and public pages canonicalise without a login. If you ever see an anonymous `308` for a protected path, the boundary has regressed.

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
- post-logout redirect URI: `https://docs.authifi.io/privacy-policy/`
- scopes: `openid profile email`

The server performs RP-initiated logout against the tenant's discovered `end_session_endpoint`, passing `client_id` and `post_logout_redirect_uri`. The redirect URI must therefore be registered with Authifi, and it must be a public path so the user is not bounced straight back into a login. When the tenant publishes no `end_session_endpoint`, the server clears the local session and redirects to that same path.

The landing path is `POST_LOGOUT_PATH`, wired end to end:

- production: the `post_logout_path` Terraform variable, which App Runner passes through as `POST_LOGOUT_PATH`. Terraform validates at plan time that it is site-relative and publicly served, so a protected path cannot reach production.
- local real and mock stacks: `POST_LOGOUT_PATH` in `.env` or the environment, read by `compose.yaml` for both overlays.

Change it in one place per environment and register the matching absolute URL with Authifi.

For local real-OIDC work, also register `http://localhost:8000/_auth/callback` and `http://localhost:8000/privacy-policy/`, or the equivalent base URL you actually use.

## Deployment Checklist

1. Bootstrap infra per the repository's [`infra/README.md`](https://github.com/Authifi/docs/blob/main/infra/README.md).
2. Confirm GitHub repository variables match Terraform outputs:
   - `AWS_REGION`
   - `AWS_DEPLOY_ROLE_ARN`
   - `APP_RUNNER_SERVICE_ARN`
   - `ECR_REPOSITORY_URL`
3. Merge to `main` and monitor `.github/workflows/deploy.yml`.
4. After the App Runner custom domain association is ready, publish the required DNS records.
5. Verify both public and protected route behavior before announcing success.

The ECR repository uses immutable tags, so rerunning the deploy workflow for a commit that was already pushed reuses the existing image and continues on to the App Runner update. Reruns are safe.

## Verification Targets

After a deploy or cutover, verify:

- `https://docs.authifi.io/` redirects to `/_auth/login` when unauthenticated
- `https://docs.authifi.io/privacy-policy/` returns `200` with `Content-Type: text/html; charset=utf-8`
- `https://docs.authifi.io/terms-of-service/` returns `200`
- `https://docs.authifi.io/sms-opt-in.html` returns `200`
- `https://docs.authifi.io/robots.txt` returns `200`
- `https://docs.authifi.io/auth.md` returns `200`
- `https://docs.authifi.io/sitemap.xml` contains only public legal URLs
- protected guides and security pages are absent from `sitemap.xml`
- `curl --path-as-is -sI 'https://docs.authifi.io/assets/%2e%2e/index.html'` returns `404`, not protected content
- protected responses carry `Cache-Control: private, no-store` and `Vary: Cookie`

## Cutover From Cloudflare Pages

The site previously served from Cloudflare Pages at `authifi.pages.dev`. The cutover is not finished until the old delivery path can no longer publish:

1. Lower the `docs.authifi.io` DNS TTL before the change window.
2. Point `docs.authifi.io` at the App Runner custom domain target and publish the certificate-validation records.
3. Wait for App Runner to report the domain association as active, then run the verification targets above.
4. **Disconnect the Cloudflare Pages Git integration** for the docs project (Pages project → Settings → Builds & deployments → disconnect the GitHub repository), or disable automatic production and preview deployments. Leaving it connected means a later push to `main` silently republishes an ungated copy of the docs.
5. Remove the `docs.authifi.io` custom domain from the Cloudflare Pages project so it cannot reclaim the hostname.
6. Keep the Pages project itself until the rollback window closes.

## Rollback Options

Choose the smallest rollback that fixes the issue:

- **DNS/edge rollback to Cloudflare Pages**: restore the `docs.authifi.io` `CNAME` to `authifi.pages.dev`, re-add `docs.authifi.io` as a custom domain on the Cloudflare Pages project, and, if it was disconnected in step 4 above, re-enable the Pages Git integration so the project can build again. Note that this restores the fully public, ungated site.
- **Image rollback**: redeploy App Runner with a previously known-good immutable SHA tag when the issue is in the built site or server. Terraform will not do this for you; use the App Runner procedure in the repository's [`infra/README.md`](https://github.com/Authifi/docs/blob/main/infra/README.md).
