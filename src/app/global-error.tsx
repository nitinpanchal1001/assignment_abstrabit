'use client';

import { useEffect } from 'react';

/**
 * Last-resort boundary, for errors thrown by the root layout itself.
 *
 * It replaces the entire document, so it must render its own <html> and
 * <body> — and it cannot rely on the app's stylesheet or theme tokens, since
 * the failure may be *in* the layout that loads them. Hence inline styles and
 * a palette that stays legible in both light and dark.
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error('[global-error]', error);
  }, [error]);

  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: '1.5rem',
          fontFamily: 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
          colorScheme: 'light dark',
        }}
      >
        <div style={{ maxWidth: '28rem' }}>
          <h1 style={{ fontSize: '1.05rem', fontWeight: 600, margin: 0 }}>
            The application failed to start
          </h1>
          <p style={{ marginTop: '0.75rem', lineHeight: 1.6, opacity: 0.75, fontSize: '0.9rem' }}>
            Something went wrong before the page could render. This is usually a configuration or
            connectivity problem rather than something you did.
          </p>
          {error.digest && (
            <p style={{ marginTop: '1rem', fontSize: '0.8rem', opacity: 0.6 }}>
              Reference: <code>{error.digest}</code>
            </p>
          )}
          <button
            type="button"
            onClick={reset}
            style={{
              marginTop: '1.25rem',
              padding: '0.5rem 1rem',
              fontSize: '0.875rem',
              fontWeight: 500,
              borderRadius: '0.5rem',
              border: '1px solid currentColor',
              background: 'transparent',
              color: 'inherit',
              cursor: 'pointer',
            }}
          >
            Reload
          </button>
        </div>
      </body>
    </html>
  );
}
