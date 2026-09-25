import { redirect } from 'next/navigation';

import { EmptyWorkspaces } from '@/components/empty-workspaces';
import { getOrNull } from '@/lib/server-api';
import type { SessionPayload } from '@/lib/types';

export const dynamic = 'force-dynamic';

/**
 * Entry point. Resolves the user's first workspace and redirects into it.
 *
 * The workspace lives in the URL rather than a cookie, so the active tenant is
 * explicit, links are shareable, and the browser's back button behaves.
 */
export default async function DashboardIndex() {
  const session = await getOrNull<SessionPayload>('/api/auth/session');

  if (!session) redirect('/login');
  if (session.workspaces.length === 0) return <EmptyWorkspaces />;

  redirect(`/dashboard/${session.workspaces[0].id}`);
}
