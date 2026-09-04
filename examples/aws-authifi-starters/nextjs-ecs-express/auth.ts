import type { NextAuthOptions } from 'next-auth';

const issuer = `${process.env.AUTHIFI_HOST}/_api/auth/${process.env.AUTHIFI_TENANT}`;
const resource = process.env.AUTHIFI_RESOURCE;

export const authOptions: NextAuthOptions = {
  providers: [
    {
      id: 'authifi',
      name: 'Authifi',
      type: 'oauth',
      wellKnown: `${issuer}/.well-known/openid-configuration`,
      clientId: process.env.AUTHIFI_CLIENT_ID ?? 'pending',
      clientSecret: process.env.AUTHIFI_CLIENT_SECRET ?? 'pending',
      authorization: {
        params: {
          scope: 'openid profile email offline_access',
          ...(resource ? { resource } : {}),
        },
      },
      token: {
        params: resource ? { resource } : {},
      },
      checks: ['pkce', 'state', 'nonce'],
      profile(profile) {
        return {
          id: profile.sub,
          name: profile.name ?? profile.preferred_username,
          email: profile.email,
        };
      },
    },
  ],
  secret: process.env.NEXTAUTH_SECRET,
  session: { strategy: 'jwt' },
};
