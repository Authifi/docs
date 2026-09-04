import { useEffect, useState } from 'react';
import type { User } from 'oidc-client-ts';
import { isConfigured, userManager } from './auth';
import './app.css';

export function App() {
  const [user, setUser] = useState<User | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const manager = userManager;
    if (!manager) return;

    const loadUser = async () => {
      try {
        if (window.location.pathname === '/callback') {
          await manager.signinRedirectCallback();
          window.history.replaceState({}, '', '/');
        }
        setUser(await manager.getUser());
      } catch (callbackError) {
        setError(
          callbackError instanceof Error
            ? callbackError.message
            : 'The Authifi callback failed.',
        );
      }
    };

    void loadUser();
  }, []);

  if (!isConfigured) {
    return (
      <main>
        <h1>Authifi SPA starter</h1>
        <p>Set the VITE_AUTHIFI_* variables before signing in.</p>
      </main>
    );
  }

  return (
    <main>
      <h1>Authifi SPA starter</h1>
      {error && <p role="alert">{error}</p>}
      {user ? (
        <>
          <p>Signed in as {user.profile.email ?? user.profile.sub}.</p>
          <button type="button" onClick={() => void userManager?.signoutRedirect()}>
            Sign out
          </button>
        </>
      ) : (
        <button type="button" onClick={() => void userManager?.signinRedirect()}>
          Sign in with Authifi
        </button>
      )}
    </main>
  );
}
