import { LoadingBlock } from '@/components/ui';

/**
 * Chat is the one page that owns the full viewport height, so it gets its own
 * fallback: the generic one would sit in a short box and the composer would
 * jump down the screen when the real panel arrived.
 */
export default function Loading() {
  return (
    <div className="flex h-screen flex-col">
      <header className="border-b border-border-base px-6 py-4">
        <h1 className="text-lg font-semibold tracking-tight">Chat</h1>
        <p className="mt-0.5 text-sm text-fg-muted">Loading this workspace&rsquo;s conversation…</p>
      </header>
      <LoadingBlock label="Loading conversation" className="flex-1" />
    </div>
  );
}
