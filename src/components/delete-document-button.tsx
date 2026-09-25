'use client';

import { useRouter } from 'next/navigation';
import { useState, useTransition } from 'react';

import { apiFetch, messageOf } from '@/lib/client/api';

import { Spinner } from './ui';

export function DeleteDocumentButton({
  documentId,
  workspaceId,
  filename,
}: {
  documentId: string;
  workspaceId: string;
  filename: string;
}) {
  const router = useRouter();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [, startTransition] = useTransition();

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      await apiFetch<void>(
        `/api/documents/${documentId}?workspaceId=${encodeURIComponent(workspaceId)}`,
        { method: 'DELETE' },
      );
      setConfirming(false);
      startTransition(() => router.refresh());
    } catch (caught) {
      // Previously a failed delete silently did nothing and closed the
      // confirm prompt, so the row stayed and looked like a UI glitch.
      setError(messageOf(caught, 'Could not delete that document.'));
    } finally {
      setBusy(false);
    }
  }

  if (!confirming) {
    return (
      <div className="flex flex-col items-end gap-1">
        {error && <span className="text-xs text-danger">{error}</span>}
      <button
        type="button"
        onClick={() => setConfirming(true)}
        className="rounded-md border border-border-base px-2.5 py-1 text-xs text-fg-muted transition-colors hover:border-danger hover:text-danger"
        aria-label={`Delete ${filename}`}
      >
        Delete
      </button>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-1.5">
      <button
        type="button"
        onClick={remove}
        disabled={busy}
        className="flex items-center gap-1.5 rounded-md bg-danger px-2.5 py-1 text-xs font-medium text-bg disabled:opacity-60"
      >
        {busy && <Spinner size={11} label={`Deleting ${filename}`} />}
        {busy ? 'Deleting…' : 'Confirm'}
      </button>
      <button
        type="button"
        onClick={() => setConfirming(false)}
        disabled={busy}
        className="rounded-md border border-border-base px-2.5 py-1 text-xs text-fg-muted"
      >
        Cancel
      </button>
    </div>
  );
}
