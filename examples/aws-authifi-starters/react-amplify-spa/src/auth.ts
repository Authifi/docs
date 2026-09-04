import { UserManager, WebStorageStateStore } from 'oidc-client-ts';

const host = import.meta.env.VITE_AUTHIFI_HOST as string | undefined;
const tenant = import.meta.env.VITE_AUTHIFI_TENANT as string | undefined;
const clientId = import.meta.env.VITE_AUTHIFI_CLIENT_ID as string | undefined;
const resource = import.meta.env.VITE_AUTHIFI_RESOURCE as string | undefined;

export const isConfigured = Boolean(host && tenant && clientId);

export const userManager = isConfigured
  ? new UserManager({
      authority: `${host}/_api/auth/${tenant}`,
      client_id: clientId!,
      redirect_uri: `${window.location.origin}/callback`,
      post_logout_redirect_uri: `${window.location.origin}/`,
      response_type: 'code',
      scope: 'openid profile email offline_access',
      loadUserInfo: true,
      userStore: new WebStorageStateStore({ store: window.sessionStorage }),
      ...(resource
        ? {
            extraQueryParams: { resource },
            extraTokenParams: { resource },
          }
        : {}),
    })
  : null;
