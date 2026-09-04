# React SPA on Amplify

This starter uses `oidc-client-ts` with Authifi authorization code flow and S256
PKCE. It is a public browser client and does not contain a client secret.

## Local development

```bash
cp .env.example .env
npm install
npm run dev
```

Set the `VITE_AUTHIFI_*` values in `.env`. Register this callback in Authifi:

```text
http://localhost:5173/callback
```

## Amplify deployment

Connect this repository and branch in Amplify Hosting. The checked-in
`amplify.yml` installs dependencies, builds the Vite output, and rewrites SPA
routes to `index.html`.

After the first deployment, register the generated URL as the Authifi callback:

```text
https://<AMPLIFY_URL>/callback
```

Add these values as Amplify build environment variables and redeploy:

```text
VITE_AUTHIFI_HOST
VITE_AUTHIFI_TENANT
VITE_AUTHIFI_CLIENT_ID
VITE_AUTHIFI_RESOURCE
```
