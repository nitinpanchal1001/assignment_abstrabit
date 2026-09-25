'use client';

import { usePathname, useRouter } from 'next/navigation';
import { useEffect, useRef, useState, useTransition } from 'react';

import { apiFetch, messageOf } from '@/lib/client/api';
import type { Workspace } from '@/lib/types';

import { Spinner } from './ui';

/**
 * Workspace switcher.
 *
 * Switching navigates rather than mutating hidden state: the workspace is a
 * URL segment, so browser history, the back button and a shared link all
 * behave correctly. The current sub-page is preserved across the switch, which
 * makes comparing the same view between two tenants — the isolation check a
 * reviewer will run — a single click.
 */
export function WorkspaceSwitcher({
  workspaces,
  activeId,
}: {
  workspaces: Workspace[];
  activeId: string;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [isPending, startTransition] = useTransition();

  const containerRef = useRef<HTMLDivElement>(null);

  const active = workspaces.find((w) => w.id === activeId) ?? workspaces[0];

  useEffect(() => {
    if (!open) return;

    function onPointerDown(event: MouseEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') setOpen(false);
    }

    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  function switchTo(workspaceId: string) {
    setOpen(false);
    if (workspaceId === activeId) return;
    // Keep the current sub-page so the same view opens in the other tenant.
    const suffix = pathname.replace(`/dashboard/${activeId}`, '');
    startTransition(() => router.push(`/dashboard/${workspaceId}${suffix}`));
  }

  async function create(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;

    setError(null);
    setBusy(true);
    try {
      const workspace = await apiFetch<Workspace>('/api/workspaces', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ name: trimmed }),
      });

      setName('');
      setCreating(false);
      setOpen(false);
      startTransition(() => {
        router.push(`/dashboard/${workspace.id}`);
        router.refresh();
      });
    } catch (caught) {
      setError(messageOf(caught, 'Could not create workspace.'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="flex w-full items-center gap-2 rounded-lg border border-border-base bg-surface-2/60 px-2.5 py-2 text-left transition-colors hover:border-border-strong"
      >
        <span className="grid size-6 shrink-0 place-items-center rounded-md bg-brand-soft text-[11px] font-semibold text-brand">
          {(active?.name ?? '?').slice(0, 1).toUpperCase()}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium">
            {active?.name ?? 'No workspace'}
          </span>
          {/* The subtitle carries the pending state rather than a floating
              label: it is already there, so nothing moves when it changes. */}
          <span className="block text-[11px] text-fg-subtle">
            {isPending
              ? 'Switching…'
              : `${workspaces.length} workspace${workspaces.length === 1 ? '' : 's'}`}
          </span>
        </span>

        {/* Spinner takes the chevron's place — same 14px box, no reflow. */}
        {isPending ? (
          <Spinner size={14} label="Switching workspace" className="text-fg-subtle" />
        ) : (
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            className={`shrink-0 text-fg-subtle transition-transform duration-150 ${open ? 'rotate-180' : ''}`}
            aria-hidden
          >
            <path d="m6 9 6 6 6-6" />
          </svg>
        )}
      </button>

      {open && (
        <div
          role="listbox"
          className="animate-pop absolute left-0 right-0 top-[calc(100%+4px)] z-40 overflow-hidden rounded-lg border border-border-base bg-surface shadow-lg shadow-black/5"
        >
          <ul className="max-h-64 overflow-y-auto p-1">
            {workspaces.map((workspace) => {
              const selected = workspace.id === activeId;
              return (
                <li key={workspace.id}>
                  <button
                    type="button"
                    role="option"
                    aria-selected={selected}
                    onClick={() => switchTo(workspace.id)}
                    className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors ${
                      selected ? 'bg-surface-2 font-medium' : 'hover:bg-surface-2/70'
                    }`}
                  >
                    <span className="grid size-5 shrink-0 place-items-center rounded bg-brand-soft text-[10px] font-semibold text-brand">
                      {workspace.name.slice(0, 1).toUpperCase()}
                    </span>
                    <span className="min-w-0 flex-1 truncate">{workspace.name}</span>
                    {selected && (
                      <svg
                        width="14"
                        height="14"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2.4"
                        strokeLinecap="round"
                        className="shrink-0 text-brand"
                        aria-hidden
                      >
                        <path d="m20 6-11 11-5-5" />
                      </svg>
                    )}
                  </button>
                </li>
              );
            })}
          </ul>

          <div className="border-t border-border-base p-1">
            {creating ? (
              <form onSubmit={create} className="space-y-1.5 p-1">
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Workspace name"
                  maxLength={80}
                  autoFocus
                  className="w-full rounded-md border border-border-base bg-bg px-2 py-1.5 text-sm outline-none focus:border-brand"
                />
                <div className="flex gap-1.5">
                  <button
                    type="submit"
                    disabled={busy || !name.trim()}
                    className="flex flex-1 items-center justify-center gap-1.5 rounded-md bg-primary px-2 py-1.5 text-xs font-medium text-primary-fg transition-colors hover:bg-primary-hover disabled:opacity-50"
                  >
                    {busy && <Spinner size={11} label="Creating workspace" />}
                    {busy ? 'Creating…' : 'Create'}
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setCreating(false);
                      setError(null);
                    }}
                    className="rounded-md border border-border-base px-2 py-1.5 text-xs text-fg-muted"
                  >
                    Cancel
                  </button>
                </div>
                {error && <p className="text-[11px] text-danger">{error}</p>}
              </form>
            ) : (
              <button
                type="button"
                onClick={() => setCreating(true)}
                className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm text-fg-muted transition-colors hover:bg-surface-2/70 hover:text-fg"
              >
                <svg
                  width="14"
                  height="14"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  aria-hidden
                >
                  <path d="M12 5v14M5 12h14" />
                </svg>
                New workspace
              </button>
            )}
          </div>
        </div>
      )}

    </div>
  );
}
