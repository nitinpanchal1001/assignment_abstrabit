'use client';

import { useEffect } from 'react';

import { ErrorState } from '@/components/error-state';

/**
 * Dashboard error boundary.
 *
 * Separate from the root boundary so a failing page keeps the sidebar — the
 * workspace switcher stays reachable, which matters because switching
 * workspace is often exactly what recovers from a workspace-specific problem.
 */
export default function DashboardError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error('[dashboard-error]', error);
  }, [error]);

  return (
    <ErrorState
      title="Couldn't load this workspace view"
      description="The page failed while fetching data. The database or search index may be briefly unavailable — try again, or switch workspace from the sidebar."
      digest={error.digest}
      onRetry={reset}
    />
  );
}
