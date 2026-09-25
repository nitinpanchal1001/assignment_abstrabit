'use client';

import { useEffect } from 'react';

import { ErrorState } from '@/components/error-state';

/**
 * Route-level error boundary.
 *
 * Catches anything thrown while rendering a Server or Client Component below
 * it. Without this, an unhandled error shows Next.js's default page, which
 * gives the user no way forward and no reference to quote.
 *
 * `reset()` re-renders the segment, which is genuinely useful here: most
 * failures reaching this boundary are transient (a datastore hiccup, a
 * rate-limited upstream) and simply retrying works.
 */
export default function RouteError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Client-side errors never reach the server log, so record them here.
    console.error('[route-error]', error);
  }, [error]);

  return (
    <ErrorState
      title="This page didn't load"
      description="Something failed while rendering this screen. It may be temporary — retrying often works."
      digest={error.digest}
      onRetry={reset}
    />
  );
}
