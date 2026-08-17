# Logout Confirmation

## Overview

Most OIDC client libraries (e.g., `oidc-client-ts`, `angular-auth-oidc-client`) clear local token data before redirecting to the identity provider's logout endpoint. This means the user's frontend session is already destroyed by the time the server processes the logout.

Choose one of two logout confirmation options:

1. **Frontend confirmation** — Show your own dialog before calling `signoutRedirect()` (or equivalent). No server-side setup needed.
2. **Authifi's `/logout/confirm` endpoint** — Override your client library's default logout to redirect to Authifi's pre-logout confirmation page instead. This preserves the user's session until they confirm.

This guide covers **Option 2**, Authifi's server-side confirmation endpoint.

!!! tip "Option 1: Frontend confirmation"
    If you prefer to handle confirmation entirely in your app, simply show a dialog before calling your OIDC library's logout method. If the user confirms, call `signoutRedirect()` (or equivalent). If they cancel, do nothing. No server-side setup is required.

---

## The `/logout/confirm` Endpoint

```
GET /auth/{tenantId}/logout/confirm
```

| Parameter                  | Required    | Description                                                                                           |
| -------------------------- | ----------- | ----------------------------------------------------------------------------------------------------- |
| `client_id`                | Recommended | Client ID. Used to look up `showLogoutPrompt` setting and apply branding.                             |
| `post_logout_redirect_uri` | Recommended | Redirect URI after logout. Also used as fallback for "stay signed in".                                |
| `id_token_hint`            | Optional    | ID token for session identification. Only used if `client_id` is not provided.                        |
| `state`                    | Optional    | Opaque value returned to your redirect URI. Use to trigger post-logout cleanup (e.g., `post_logout`). |
| `show_prompt`              | Optional    | Override `showLogoutPrompt`. `"true"` = show prompt, `"false"` = skip to `/session/end`.              |

The confirmation page presents the user with:

- **"Yes, sign me out"** — Proceeds with logout.
- **"No, stay signed in"** — Returns to the application with the session fully intact.
- **30-second auto-logout** — If no action is taken, the user is automatically signed out.
- **Active session info** — Shows which other applications will be affected by the logout.

### `showLogoutPrompt` Setting

Configured per-client in the Admin UI under **Login/Logout** settings:

- `true` (default) — Shows the confirmation page.
- `false` — Redirects directly to `/session/end`, skipping confirmation.

!!! note
    This setting only affects the `/logout/confirm` endpoint. The standard `/session/end` endpoint always logs the user out immediately regardless of this setting.

---

## Integration Pattern

Use this flow with any OIDC library.

!!! warning "Important"
    **Do not** call your library's logout method (`signoutRedirect()`, `logoff()`, `signOut()`, etc.) when using this flow. These methods clear local tokens before redirecting, which defeats the purpose of the confirmation.

### Step 1: Redirect to `/logout/confirm`

When the user clicks "Log out", navigate to the confirmation endpoint instead of calling your OIDC library's logout method:

```typescript
function logout() {
  const params = new URLSearchParams({
    client_id: 'your-client-id',
    post_logout_redirect_uri: 'https://your-app.com',
    state: 'post_logout'
  });

  window.location.href = `https://your-auth-server.com/_api/auth/{tenantId}/logout/confirm?${params}`;
}
```

### Step 2: Handle Post-Logout Cleanup

At app initialization, check for the `state` parameter and clean up local session data:

```typescript
function handlePostLogoutCleanup(): boolean {
  const urlParams = new URLSearchParams(window.location.search);
  if (urlParams.get('state') !== 'post_logout') {
    return false;
  }

  // Clean up URL
  const url = new URL(window.location.href);
  url.searchParams.delete('state');
  window.history.replaceState({}, document.title, url.toString());

  // Clear OIDC storage
  for (const storage of [localStorage, sessionStorage]) {
    Object.keys(storage).forEach((key) => {
      if (key.startsWith('oidc.')) {
        storage.removeItem(key);
      }
    });
  }

  // Clear library-specific state:
  // - oidc-client-ts: userManager.removeUser()
  // - angular-auth-oidc-client: oidcSecurityService.logoffLocal()
  // - next-auth: signOut({ redirect: false })

  return true;
}

// On app startup:
if (!handlePostLogoutCleanup()) {
  // Normal auth initialization
}
```

### Cancellation

If the user clicks "No, stay signed in", the browser navigates back to the application. No tokens were cleared, so the session is fully preserved. **No action is needed in your app.**

---

## Skipping the Confirmation

To use `/logout/confirm` without showing the prompt, such as during an idle timeout:

- Pass `show_prompt=false` as a query parameter:

    ```
    /auth/{tenantId}/logout/confirm?client_id=X&post_logout_redirect_uri=Y&show_prompt=false
    ```

- Or set `showLogoutPrompt: false` in the client's configuration via the Admin UI.
