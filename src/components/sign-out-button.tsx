'use client';

import { useRouter } from 'next/navigation';
import { useState } from 'react';

import { apiFetch } from '@/lib/client/api';

import { Spinner } from './ui';

export function SignOutButton() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);

  return (
    <button
      type="button"
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        // Sign-out should never trap the user: if the request fails, clear the
        // client state and redirect anyway rather than leaving them stuck on a
        // page they are trying to leave.
        await apiFetch('/api/auth/logout', { method: 'POST' }).catch(() => undefined);
        router.replace('/login');
        router.refresh();
      }}
      className="flex w-full items-center justify-center gap-1.5 rounded-md border border-border-base px-3 py-1.5 text-xs font-medium text-fg-muted transition-colors hover:bg-surface-2 hover:text-fg disabled:opacity-60"
    >
      {busy && <Spinner size={12} label="Signing out" />}
      {busy ? 'Signing out…' : 'Sign out'}
    </button>
  );
}
