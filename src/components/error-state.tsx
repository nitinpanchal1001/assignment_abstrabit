'use client';

import Link from 'next/link';
import { useTransition } from 'react';

import { Spinner } from './ui';

/**
 * Shared presentation for a failed screen.
 *
 * Next.js passes a `digest` for server-side errors — the same value is printed
 * in the server log for that failure, so showing it turns "it broke" into
 * something traceable. The error *message* is deliberately not shown for
 * server errors: Next replaces it with a generic string in production anyway,
 * and in development it can contain internal detail.
 */
export function ErrorState({
  title,
  description,
  digest,
  onRetry,
  retryLabel = 'Try again',
}: {
  title: string;
  description: string;
  digest?: string;
  onRetry?: () => void;
  retryLabel?: string;
}) {
  // Retrying re-runs the server component that failed, which means another
  // round trip to the API. Without a pending state the button looks inert and
  // invites a second click on something that is already running.
  const [retrying, startRetry] = useTransition();

  return (
    <div className="mx-auto flex min-h-[60vh] max-w-md flex-col justify-center px-6 py-12">
      <div className="rounded-xl border border-border-base bg-surface p-6">
        <div className="flex size-9 items-center justify-center rounded-lg bg-danger-soft text-danger">
          <svg
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            aria-hidden
          >
            <path d="M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
          </svg>
        </div>

        <h1 className="mt-4 text-base font-semibold tracking-tight">{title}</h1>
        <p className="mt-2 text-sm leading-relaxed text-fg-muted">{description}</p>

        {digest && (
          <p className="mt-4 text-xs text-fg-subtle">
            Reference: <code className="font-mono">{digest}</code>
            <br />
            Quote this if you report the problem — it identifies this exact failure in the logs.
          </p>
        )}

        <div className="mt-5 flex flex-wrap gap-2">
          {onRetry && (
            <button
              type="button"
              disabled={retrying}
              onClick={() => startRetry(() => onRetry())}
              className="flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-fg transition-colors hover:bg-primary-hover disabled:opacity-60"
            >
              {retrying && <Spinner size={14} label="Retrying" />}
              {retrying ? 'Retrying…' : retryLabel}
            </button>
          )}
          <Link
            href="/dashboard"
            className="rounded-lg border border-border-base px-4 py-2 text-sm font-medium text-fg-muted transition-colors hover:bg-surface-2 hover:text-fg"
          >
            Back to dashboard
          </Link>
        </div>
      </div>
    </div>
  );
}
