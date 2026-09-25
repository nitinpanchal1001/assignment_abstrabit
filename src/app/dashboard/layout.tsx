import { redirect } from 'next/navigation';

import { AppShell } from '@/components/app-shell';
import { getOrNull } from '@/lib/server-api';
import type { SessionPayload } from '@/lib/types';

export const dynamic = 'force-dynamic';

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
