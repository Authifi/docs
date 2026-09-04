import { getServerSession } from 'next-auth';
import { redirect } from 'next/navigation';
import { authOptions } from '@/auth';

export default async function Dashboard() {
  const session = await getServerSession(authOptions);

  if (!session) {
    redirect('/api/auth/signin/authifi?callbackUrl=/dashboard');
  }

  return (
    <main>
      <h1>Dashboard</h1>
      <p>Signed in as {session.user?.email ?? session.user?.name}.</p>
      <a href="/api/auth/signout">Sign out</a>
    </main>
  );
}
