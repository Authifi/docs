import { getServerSession } from 'next-auth';
import { authOptions } from '@/auth';

export default async function Home() {
  const session = await getServerSession(authOptions);

  return (
    <main>
      <h1>Authifi ECS Express starter</h1>
      {session ? (
        <p>
          <a href="/dashboard">Open the protected dashboard</a>
        </p>
      ) : (
        <p>
          <a href="/login">Sign in with Authifi</a>
        </p>
      )}
    </main>
  );
}
