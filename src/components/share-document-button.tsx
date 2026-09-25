'use client';

import { useRouter } from 'next/navigation';
import { useState } from 'react';

import { apiFetch, messageOf } from '@/lib/client/api';
import type { DocumentShare, Workspace } from '@/lib/types';

import { Spinner } from './ui';

/**
 * Grant or revoke another workspace's read access to one document.
 *
 * The affordance is deliberately modest. Sharing crosses a tenancy boundary,
 * so it should look like a decision rather than a convenience: the control is
 * quiet until opened, every grant names the workspace it is giving access to,
 * and each one can be withdrawn from the same place it was made — a grant you
 * cannot find is a grant you cannot revoke.
 *
 * Only workspaces the signed-in user already belongs to are offered. That
 * mirrors the API, which authorises the target with a second membership check;
 * offering a workspace here that the server would refuse would be a worse
 * experience than not offering it at all.
 */
export function ShareDocumentButton({
  documentId,
  workspaceId,
  filename,
  workspaces,
}: {
  documentId: string;
  workspaceId: string;
  filename: string;
  /** Every workspace the user belongs to, including the active one. */
  workspaces: Workspace[];
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [shares, setShares] = useState<DocumentShare[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // A document cannot be shared with the workspace that already owns it.
  const candidates = workspaces.filter((w) => w.id !== workspaceId);

  async function load() {
    setError(null);
    try {
      setShares(
        await apiFetch<DocumentShare[]>(
          `/api/documents/${documentId}/shares?workspaceId=${workspaceId}`,
        ),
      );
    } catch (caught) {
      setError(messageOf(caught, 'Could not load sharing for this document.'));
    }
  }

  async function toggle() {
    const next = !open;
    setOpen(next);
    if (next && shares === null) await load();
  }

  async function grant(target: Workspace) {
    setBusy(target.id);
    setError(null);
    try {
      await apiFetch(`/api/documents/${documentId}/shares?workspaceId=${workspaceId}`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ targetWorkspaceId: target.id }),
      });
      await load();
      // The borrowing workspace's "shared with this workspace" list lives on
      // another route, so refresh rather than patching local state.
      router.refresh();
    } catch (caught) {
      setError(messageOf(caught, `Could not share with ${target.name}.`));
    } finally {
      setBusy(null);
    }
  }

  async function revoke(share: DocumentShare) {
    setBusy(share.targetWorkspaceId);
    setError(null);
    try {
      await apiFetch(
        `/api/documents/${documentId}/shares/${share.targetWorkspaceId}?workspaceId=${workspaceId}`,
        { method: 'DELETE' },
      );
      await load();
      router.refresh();
    } catch (caught) {
      setError(messageOf(caught, `Could not revoke access for ${share.targetWorkspaceName}.`));
    } finally {
      setBusy(null);
    }
  }

  if (candidates.length === 0) return null;

  const sharedIds = new Set((shares ?? []).map((s) => s.targetWorkspaceId));
  const available = candidates.filter((w) => !sharedIds.has(w.id));

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => void toggle()}
        aria-expanded={open}
        aria-label={`Share ${filename} with another workspace`}
        className="flex items-center gap-1.5 rounded-md border border-border-base px-2.5 py-1.5 text-xs text-fg-muted transition-colors hover:border-brand hover:text-brand"
      >
        <svg
          width="13"
          height="13"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden
        >
          <circle cx="18" cy="5" r="3" />
          <circle cx="6" cy="12" r="3" />
          <circle cx="18" cy="19" r="3" />
          <path d="M8.6 13.5l6.8 4M15.4 6.5l-6.8 4" />
        </svg>
        Share
        {sharedIds.size > 0 && (
          <span className="rounded-full bg-brand-soft px-1.5 text-[10px] font-semibold text-brand">
            {sharedIds.size}
          </span>
        )}
      </button>

      {open && (
        <div className="animate-rise absolute right-0 z-20 mt-1.5 w-72 rounded-lg border border-border-base bg-surface p-1.5 shadow-lg shadow-black/10">
          <p className="px-2 py-1.5 text-[11px] leading-relaxed text-fg-subtle">
            Give another workspace <span className="font-medium text-fg-muted">read-only</span>{' '}
            access to this document. It stays owned by this workspace and can be withdrawn at any
            time.
          </p>

          {shares === null ? (
            <p className="flex items-center gap-2 px-2 py-2 text-xs text-fg-subtle">
              <Spinner size={12} label="Loading sharing" />
              Loading…
            </p>
          ) : (
            <>
              {shares.length > 0 && (
                <ul className="mt-1 space-y-0.5 border-t border-border-base pt-1.5">
                  {shares.map((share) => (
                    <li
                      key={share.id}
                      className="flex items-center gap-2 rounded px-2 py-1.5 text-xs"
                    >
                      <span className="size-1.5 shrink-0 rounded-full bg-success" aria-hidden />
                      <span className="min-w-0 flex-1 truncate" title={share.targetWorkspaceName}>
                        {share.targetWorkspaceName}
                      </span>
                      <button
                        type="button"
                        onClick={() => void revoke(share)}
                        disabled={busy !== null}
                        className="shrink-0 font-medium text-danger transition-opacity hover:underline disabled:opacity-50"
                      >
                        {busy === share.targetWorkspaceId ? 'Revoking…' : 'Revoke'}
                      </button>
                    </li>
                  ))}
                </ul>
              )}

              {available.length > 0 && (
                <ul className="mt-1 space-y-0.5 border-t border-border-base pt-1.5">
                  {available.map((workspace) => (
                    <li key={workspace.id}>
                      <button
                        type="button"
                        onClick={() => void grant(workspace)}
                        disabled={busy !== null}
                        className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs transition-colors hover:bg-surface-2 disabled:opacity-50"
                      >
                        <span
                          className="size-1.5 shrink-0 rounded-full bg-border-base"
                          aria-hidden
                        />
                        <span className="min-w-0 flex-1 truncate">{workspace.name}</span>
                        <span className="shrink-0 text-fg-subtle">
                          {busy === workspace.id ? 'Sharing…' : 'Share'}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}

              {available.length === 0 && shares.length > 0 && (
                <p className="px-2 py-1.5 text-[11px] text-fg-subtle">
                  Shared with every other workspace you belong to.
                </p>
              )}
            </>
          )}

          {error && (
            <p role="alert" className="px-2 py-1.5 text-[11px] text-danger">
              {error}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
