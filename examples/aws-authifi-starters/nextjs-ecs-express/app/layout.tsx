import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Authifi ECS Express starter',
  description: 'Minimal Next.js BFF protected by Authifi',
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
