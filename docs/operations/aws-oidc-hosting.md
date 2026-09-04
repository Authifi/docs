# AWS OIDC Hosting Notes

This note supplements the repository's [`infra/README.md`](https://github.com/Authifi/docs/blob/main/infra/README.md) with repo-specific operating guidance for the Authifi docs site's AWS and OIDC hosting model. Use that file for the canonical Terraform bootstrap commands.

## Runtime Overview

- MkDocs produces the static site in `site/`.
- The Starlette server in `server/` serves that build, keeps specific legal and discovery paths public, and redirects protected content through Authifi OIDC.
- The production container image is built from `Dockerfile`, pushed to ECR, and run on AWS App Runner.

## What `/health` Means

`/health` reports whether this process can serve the site, not whether it is
running. It reads two artifacts out of `SITE_DIR` and answers `200` with
`{"status": "ok"}` only if both open and are non-empty:

- `index.html`, the front page
- the page `POST_LOGOUT_PATH` names, `privacy-policy/index.html` by default,
  which is also the compliance document that has to stay publicly reachable

Anything else is `503` with `{"status": "unavailable"}`. That covers a
`SITE_DIR` pointing at nothing, a runtime stage that shipped without its build,
an artifact the container's uid cannot read, and a zero-byte page from a failed
build. Without this, a deployment whose site is missing answers `404` to every
request while reporting itself healthy, and App Runner routes production traffic
to it; the Compose healthcheck reads the body, so `503` marks the container
unhealthy there too.

The response deliberately names neither the paths nor the directory: `/health`
is served to anyone. The list of artifacts that failed goes to the container
log instead, so `docker compose logs docs` or the App Runner application log is
where you find out which one it was.

## Authorization Policy

Authorization in v1 is **authentication only**: any identity that the configured Authifi tenant accepts through the OIDC flow may read every protected page. The server stores the subject, and optionally the email and name, and grants access on that basis alone.

Each of those three claims is bounded in UTF-8 bytes, because the issuer, not
this server, decides how long they are, and all of them ride in a signed cookie
the browser silently discards past 4096 bytes:

| Claim | Limit | Over the limit |
|---|---|---|
| `sub` | 255 bytes | sign-in fails closed, no session |
| `email` | 254 bytes (RFC 5321 maximum) | claim dropped, sign-in succeeds |
| `name` | 128 bytes | claim dropped, sign-in succeeds |

The optional claims are dropped rather than truncated: half an email address is
a wrong one, and access depends on the subject alone, so losing a display value
is better than refusing a legitimate login. A missing, malformed, or overlong
subject is fatal instead, since there is no session without one. The worst
`Set-Cookie` this design can produce -- all three claims at their ceiling, a
login just completed, and the other tabs still holding pending logins at the
return-path cap -- measures 3282 bytes, leaving a little over 800 spare.

There is deliberately **no group, role, or email-domain filtering in v1**. Controlling who can read the docs is therefore controlled entirely by controlling who can sign in to the configured tenant and who is assigned the docs application. If finer-grained access becomes a requirement, it is a follow-up change to the callback handler and the session contents, not a configuration toggle.

The boundary also hides the shape of the protected tree. Authorization runs before trailing-slash canonicalisation for protected routes, so an anonymous request for `/guides/sso-integration-guide` and one for a page that does not exist both answer `307` to `/_auth/login`, with `next` echoing the path exactly as requested. Signed-in callers get the usual `308` to the canonical form, and public pages canonicalise without a login. If you ever see an anonymous `308` for a protected path, the boundary has regressed.

### Several Login Tabs At Once

Opening two gated pages starts two logins against one session cookie, and they
may finish in either order. Each login gets its own OAuth state; the page it
should return to is stored under that state, and a callback consumes only its
own entry, so both tabs land where they started.

Four concurrent logins are kept. Starting a fifth evicts the oldest, because
each pending transaction costs space in a signed cookie that the browser
silently discards once it passes roughly 4KB. A callback for an evicted,
already-used, or unrecognised state answers `400` and leaves the other pending
logins untouched — a forged callback must not be able to cancel someone else's
sign-in. Users see this only if they leave more than four sign-ins half-finished
at once; the fix is to start again from the page they wanted.

The stored return path is capped at 256 UTF-8 bytes, which is what makes that
budget an upper bound rather than something a caller picks: `next` arrives in
the query string, so an uncapped one is a way to push the cookie past 4096
bytes and have the browser drop it. Four pending logins at the cap measure a
little over 3KB, and the longest path the site publishes is 63 bytes, so the cap
leaves well over 100 bytes for a query string on top. A `next` over the cap is
not truncated — it becomes `/`, so an over-long link signs you in and lands you
on the home page rather than somewhere half-parsed.

A pending login also has a shelf life. Authlib stamps each stored transaction
with an hour's expiry and sweeps the expired ones the next time any callback
completes, so a tab left open overnight gets the same `400` rather than a `500`.
Nothing is exchanged with the issuer in that case, and the other tabs are
unaffected.

### When The Issuer Declines

A user who presses "no" at the login screen, or whose sign-in the issuer refuses
for any other reason, comes back to `/_auth/callback` with an `error` parameter
instead of a code. That is an expected outcome, not a fault: it answers `400`,
no token endpoint is called, and nothing the issuer said — neither the error
code nor its description — is echoed back to the browser. The error code alone
is logged, and only when it looks like the short protocol token it should be,
since both fields arrive as query parameters and are attacker-controlled.

Only that tab's sign-in ends. The other pending logins survive and can still
complete, and an existing signed-in session is left alone: refusing a *new*
authorization says nothing about an identity that was already verified, so
ending that session here would be a denial of service dressed up as caution.

### A Sign-In Lasts Eight Hours, However Busy The Tab

There are two clocks and they are not the same one.

The session cookie carries `max_age=28800`, and Starlette re-issues that cookie
on every response that carries a session. So that clock restarts with each page
view: it expires a browser that was left alone, and nothing else. On its own it
made the eight hours the cookie advertised a fiction — a tab open and
occasionally clicked never expired.

The callback therefore stamps the session with the moment it authenticated, and
every protected request measures against that stamp. At eight hours the session
is over regardless of activity: the next protected request clears the cookie and
answers `307` to `/_auth/login`, and the user goes back through the issuer.
Signing in again is a new authentication with a new stamp, so a lapsed session
is not a lockout — including from the tab whose pending sign-in was still in
flight.

Both clocks stay, because they answer different questions. `max_age` is what
stops a cookie being replayed a month later, and the signature check that
enforces it happens before the application sees the session at all.

A session with no stamp, one that will not parse as a number, or one dated in
the future is not a session. Sign-ins predate this rule, and a replayed cookie's
contents are whatever the replayer chose; neither is a reason to let a request
through. `NaN` is refused explicitly, since every comparison against it is false
and an age check written the obvious way would wave it past.

One consequence worth knowing when debugging: an expired session is not treated
as signed in by logout either. It gets the same local redirect an anonymous
caller gets, and no outbound request to the issuer.

### Signing Out Ends Everything, Not Just This Tab

Every gated page carries a **Sign out** control posting to `/_auth/logout`, by
one of two routes. Pages Material renders get it from the header partial, next
to the palette and search controls. Public pages omit it — there is no session
to end there.

The control is a `<form method="post">` with a submit button, not a link,
because the route answers `POST` only. `docs/stylesheets/authifi.css` strips the
operating system's push-button chrome off the header one so it looks like the
controls beside it, inheriting Material's own colour and focus outline rather
than restating them. Nothing about either control needs JavaScript.

`feature-list.html` is generated upstream in idbroker and copied into the site
verbatim, so it never passes through a template and carries a notice not to edit
it here. The post-build hook in `docs/hooks/agent_assets.py` adds the link to
the *built artifact* instead: a `<nav aria-label="Session">` after `<body>` and
its own self-contained styling before `</head>`, since that page never loads
Material's stylesheet. The source file is left byte-identical, which a test
checks by asking `git` whether the build dirtied it.

Two properties of that injection matter if you touch it. It is idempotent, keyed
on a sentinel comment, because `mkdocs build --dirty` reuses the site directory
and would otherwise run over an already-augmented copy. And it fails the build
if the insertion points are missing or ambiguous, or if a listed artifact was
never built: the file is upstream's to change, and quietly doing nothing would
ship a gated page with no way out of the session. If an upstream reshape breaks
the build, fix the insertion points rather than dropping the page from
`AUGMENTED_ARTIFACTS`.

### Only A POST From This Site Can End A Session

`GET /_auth/logout` answers `405` with `Allow: POST`. It clears nothing and does
not contact the issuer. The route accepts the method purely so that reply can be
sent: without it the catch-all site route would answer `404`, which tells
somebody following an old bookmark nothing about how to sign out. A `HEAD` is
refused the same way, and neither refusal is cacheable.

Every `POST` must carry an `Origin` header whose origin matches the one
`PUBLIC_BASE_URL` names. Anything else — another site, our host on another port,
a downgraded scheme, `null`, an unparseable value, or no header at all — is
refused with `403` before the session is touched and before any outbound
request. Comparison is between parsed origins, not strings, so `https://host`
and `https://host:443` match and host case is ignored; matching the header
verbatim would refuse legitimate submissions on those grounds. The header is
held to a stricter shape than the configuration: a path, query, fragment, or
credentials in it means it is not something a browser sent, while
`PUBLIC_BASE_URL` may legitimately carry a sub-path.

A refusal logs only the *shape* of the header — missing, or not this site. The
value is attacker-chosen and unbounded, so putting it in the log would be
somebody else writing to your logs, and the response does not echo it either.

`PUBLIC_BASE_URL` is therefore validated at startup, not at the first logout. A
value that is not an absolute `http` or `https` URL fails the container
immediately instead of letting it serve traffic and refuse every sign-out.

### Signing Out Clears Everything, Not Just This Tab

`/_auth/logout` clears the whole session cookie: the signed-in identity and
every in-flight sign-in in every other tab. That is deliberate — signing out
must not leave a half-finished transaction behind that could still be completed
afterwards.

The visible consequence is that a tab which was mid-login when the user signed
out elsewhere fails safely with the same `400` when its callback finally
arrives. It cannot resume, and the user has to start that sign-in again from the
page they wanted. If you are reproducing a report of "logging out broke my other
tab", this is the expected behaviour rather than a fault.

## Local Mock Networking

OIDC requires every party to agree on one issuer URL, so the single hostname in
`MOCK_OIDC_HOST` has to resolve for two clients that sit on different networks:
the docs container, and the host running the smoke client. `compose.mock.yaml`
resolves each half separately.

- **From the docs container**, the hostname is a Compose network alias on the
  `mock-oidc` service, so it resolves to that container's address on the default
  network. No host involvement and no DNS lookup.
- **From the host**, the provider publishes `$MOCK_OIDC_PORT` (`9400` by
  default) on `127.0.0.1` only, and the hostname has to resolve to loopback
  (see below).

Both halves therefore answer to `http://$MOCK_OIDC_HOST:$MOCK_OIDC_PORT` while
the container's traffic stays inside Docker's network.

Because the issuer URL carries the port and the container dials the provider
directly, `MOCK_OIDC_PORT` has to move the provider's own listen port, not just
the host mapping: `compose.mock.yaml` passes it to the provider as `--port`,
publishes it unshifted, and uses it in the healthcheck. Publishing `9500:9400`
would satisfy the host and leave the container dialling a closed port.

This is not merely tidier than the previous `extra_hosts: <host>:host-gateway`
mapping on the `docs` service — that mapping was broken on Linux. It pointed the
container at the host's gateway address, but the provider is published on
loopback, which a container cannot reach through the gateway. Docker Desktop
routes it to the host's loopback anyway and so masked the bug; a standard Linux
engine surfaced it as a `500` from `/_auth/login` when Authlib fetched
discovery. Keep the port loopback-only and let the alias carry container
traffic. `server/tests/test_compose.py` renders the stack and fails if
`host-gateway` returns, the alias goes missing, or `MOCK_OIDC_PORT` stops
reaching every place the port appears.

### The Smoke Runner's URL Overrides Configure The Stack

`server/local_smoke.py` takes `--public-base-url` and `--mock-issuer`, and both
now build the Compose environment rather than only the client that dials it.
`--public-base-url` sets `PUBLIC_BASE_URL` and derives the published
`DOCS_PORT`; `--mock-issuer` sets `MOCK_OIDC_HOST` and `MOCK_OIDC_PORT`, which
is what moves the alias, the published mapping, the provider's `--port`, and the
`OIDC_ISSUER` the docs container is given. The client's settings are then read
back out of that environment, one direction only, so the stack cannot be
configured for one set of URLs while the assertions are written against
another.

Previously they configured only the client, which made every override a broken
run: `--public-base-url http://localhost:9001` left the docs container published
on 8000 and told it its base URL was `http://localhost:8000`, and
`--mock-issuer` left the provider under its old alias and port. The second shape
got worse once logout began checking `Origin` against `PUBLIC_BASE_URL` — a
client dialling 9001 against a server told 8000 has every sign-out refused, and
the smoke would report a CSRF regression that existed only in the harness. The
same inconsistency was reachable without the CLI at all, by setting
`PUBLIC_BASE_URL` in `.env` without a matching `DOCS_PORT`; deriving the port
fixes that route too.

Both URLs must be something these stacks could answer on: `http://` (neither
stack terminates TLS), no path, query, fragment, or credentials, an explicit
port from 1024 to 65535 — it becomes a published mapping and the provider's own
listen port, and a scheme default of 80 would need privileges to bind — and a
host resolving only to loopback. Loopback literals and `.localhost` names are
taken as such without a lookup; anything else is resolved, and one routable
address is enough to refuse. The default `nip.io` name and CI's `/etc/hosts`
alias both qualify. Validation runs before Docker is touched and names the
option in the message.

That last rule is a safety guard, not tidiness. The runner tears its stack down
with `--volumes` and writes a test user into whatever issuer URL it is handed,
so being able to point it at a non-local address is not a capability worth
having.

### The Default Mock Hostname Needs Public DNS On The Host

`MOCK_OIDC_HOST` defaults to `oidc-mock.127.0.0.1.nip.io`, a public wildcard
resolver that maps any `<anything>.127.0.0.1.nip.io` name to `127.0.0.1`. Only
the host side needs this; the container uses the network alias either way. The
cost is a dependency on a third-party public resolver, which fails in two
situations that look like a broken mock rather than a broken lookup:

- **Offline or network-restricted machines.** `nip.io` is a real DNS lookup, so
  an air-gapped laptop or a CI runner with egress filtering cannot resolve it.
- **DNS rebinding protection.** Many home routers, corporate resolvers, and
  systemd-resolved configurations drop answers that map a public name to a
  loopback or private address, which is exactly what `nip.io` returns. The
  lookup does not fail cleanly; it returns `NXDOMAIN` or an empty answer.

The fix in both cases is to stop depending on public DNS. Pick a name, resolve
it locally, and point `MOCK_OIDC_HOST` at it:

```bash
echo "127.0.0.1 oidc-mock.local.test" | sudo tee -a /etc/hosts
echo "MOCK_OIDC_HOST=oidc-mock.local.test" >> .env
```

The alias follows whatever you set, so the container needs no matching
`/etc/hosts` entry. CI does exactly this, which is why the workflow has no
`nip.io` dependency. Use `.local.test` or another reserved suffix rather than a
name that could later resolve publicly.

A `500` from `/_auth/login` means the docs container could not load discovery
from the issuer. `make local-mock-up` failures dump `docs` and `mock-oidc`
container logs before tearing the stack down; start there.

## Production OIDC Registration

The Authifi application for this docs host should be a confidential Web App with:

- callback URL: `https://docs.authifi.io/_auth/callback`
- post-logout redirect URI: `https://docs.authifi.io/privacy-policy/`
- scopes: `openid profile email`

The server performs RP-initiated logout against the tenant's discovered `end_session_endpoint`, passing `client_id` and `post_logout_redirect_uri`. The redirect URI must therefore be registered with Authifi, and it must be a public path so the user is not bounced straight back into a login. When the tenant publishes no `end_session_endpoint`, the server clears the local session and redirects to that same path.

The landing path is `POST_LOGOUT_PATH`, wired end to end:

- production: the `post_logout_path` Terraform variable, which App Runner passes through as `POST_LOGOUT_PATH`. Terraform validates at plan time that it is site-relative and one of the exact public pages, so a protected path cannot reach production.
- local real and mock stacks: `POST_LOGOUT_PATH` in `.env` or the environment, read by `compose.yaml` for both overlays.

The server re-checks the value at startup against the same list and refuses to start if it fails, so a misconfigured local stack fails on `make local-up` rather than silently misbehaving at the first logout. The list is the exact public paths only; the public prefixes serve stylesheets, scripts, and well-known documents, none of which is a page to land on.

Two behaviours worth knowing when reading logs:

- A `?next=` on `/_auth/logout` is ignored. `post_logout_redirect_uri` is always the configured path, because Authifi matches it against the registered URI exactly and would reject anything else.
- Logout with no session never contacts the issuer. There is nothing to end, and it stops an anonymous caller from driving outbound metadata requests by hitting the route repeatedly.

Change it in one place per environment and register the matching absolute URL with Authifi.

For local real-OIDC work, also register `http://localhost:8000/_auth/callback` and `http://localhost:8000/privacy-policy/`, or the equivalent base URL you actually use.

## Deployment Checklist

1. Bootstrap infra per the repository's [`infra/README.md`](https://github.com/Authifi/docs/blob/main/infra/README.md).
2. Confirm GitHub repository variables match Terraform outputs:
   - `AWS_REGION`
   - `AWS_DEPLOY_ROLE_ARN`
   - `APP_RUNNER_SERVICE_ARN`
   - `ECR_REPOSITORY_URL`
   - `APP_RUNNER_SERVICE_URL` (optional; overrides the origin the post-deploy check probes)
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
- `https://docs.authifi.io/health` returns `200` with `{"status": "ok"}`; a
  `503` means the deployment is serving an incomplete site and the container log
  names the artifact

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
