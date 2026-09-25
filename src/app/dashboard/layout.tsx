import { redirect } from 'next/navigation';

import { AppShell } from '@/components/app-shell';
import { getOrNull } from '@/lib/server-api';
import type { SessionPayload } from '@/lib/types';

export const dynamic = 'force-dynamic';
/**
 * The API sleeps on Render's free tier and takes up to a minute to wake. A
 * Vercel function defaults to a 10s timeout on Hobby, so a cold backend would
 * fail this render outright; 60s is the Hobby ceiling. The proxied /api/*
 * rewrite is separate and gets 120s, so streaming a chat turn is unaffected.
 */
export const maxDuration = 60;


/**
 * Loads the session once for the whole dashboard.
 *
 * Every nested page needs the user and their workspace list; fetching it here
 * means one API round trip per navigation instead of one per page.
 */
export default async function DashboardLayout({ children }: { children: React.ReactNode }) {
  const session = await getOrNull<SessionPayload>('/api/auth/session');
  if (!session) redirect('/login');

  return (
    <AppShell user={session.user} workspaces={session.workspaces}>
      {children}
    </AppShell>
  );
}
