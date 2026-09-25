'use client';

import Link, { useLinkStatus } from 'next/link';
import { useParams, usePathname } from 'next/navigation';
import { useState } from 'react';

import type { User, Workspace } from '@/lib/types';

import { SignOutButton } from './sign-out-button';
import { ThemeToggle } from './theme-toggle';
import { Spinner } from './ui';
import { WorkspaceSwitcher } from './workspace-switcher';

const NAV = [
  { segment: '', label: 'Overview', icon: GridIcon },
  { segment: '/chat', label: 'Chat', icon: ChatIcon },
  { segment: '/documents', label: 'Documents', icon: DocIcon },
  { segment: '/tasks', label: 'Tasks', icon: CheckIcon },
  { segment: '/activity', label: 'Tool activity', icon: PulseIcon },
  { segment: '/insights', label: 'Insights', icon: ChartIcon },
] as const;

export function AppShell({
  user,
  workspaces,
  children,
}: {
  user: User;
  workspaces: Workspace[];
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const params = useParams<{ workspaceId?: string }>();
  const [mobileOpen, setMobileOpen] = useState(false);

  const workspaceId = params?.workspaceId ?? workspaces[0]?.id ?? '';
  const base = `/dashboard/${workspaceId}`;

  const sidebar = (
    <div className="flex h-full flex-col gap-5 p-4">
      <div className="flex items-center justify-between">
        <Link href="/dashboard" className="group flex items-center gap-2">
          <span className="grid size-7 place-items-center rounded-lg bg-primary text-primary-fg">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden>
              <path
                d="M4 7h16M4 12h10M4 17h7"
                stroke="currentColor"
                strokeWidth="2.2"
                strokeLinecap="round"
              />
            </svg>
          </span>
          <span className="text-sm font-semibold tracking-tight">Groundwork</span>
        </Link>

        <button
          type="button"
          onClick={() => setMobileOpen(false)}
          className="rounded-md p-1 text-fg-subtle hover:text-fg lg:hidden"
          aria-label="Close menu"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden>
            <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
        </button>
      </div>

      <WorkspaceSwitcher workspaces={workspaces} activeId={workspaceId} />

      <nav className="flex flex-col gap-0.5">
        {NAV.map(({ segment, label, icon: Icon }) => {
          const href = `${base}${segment}`;
          const active = segment === '' ? pathname === base : pathname.startsWith(href);

          return (
            <Link
              key={label}
              href={href}
              onClick={() => setMobileOpen(false)}
              aria-current={active ? 'page' : undefined}
              className={`group relative flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm transition-all duration-150 ${
                active
                  ? 'bg-surface-2 font-medium text-fg'
                  : 'text-fg-muted hover:bg-surface-2/60 hover:text-fg'
              }`}
            >
              {/* Active marker as a bar rather than a background tint: it reads
                  at a glance without adding another block of colour. */}
              <span
                className={`absolute left-0 h-4 w-0.5 rounded-full bg-brand transition-opacity duration-150 ${
                  active ? 'opacity-100' : 'opacity-0'
                }`}
              />
              <Icon />
              {label}
              <NavPending />
            </Link>
          );
        })}
      </nav>

      <div className="mt-auto space-y-3">
        <ThemeToggle />
        <div className="rounded-lg border border-border-base bg-surface-2/50 p-2.5">
          <p className="truncate text-xs font-medium" title={user.email}>
            {user.displayName}
          </p>
          <p className="truncate text-[11px] text-fg-subtle" title={user.email}>
            {user.email}
          </p>
          <div className="mt-2">
            <SignOutButton />
          </div>
        </div>
      </div>
    </div>
  );

  return (
    <div className="flex min-h-screen">
      {/* Desktop sidebar */}
      <aside className="sticky top-0 hidden h-screen w-64 shrink-0 border-r border-border-base bg-surface lg:block">
        {sidebar}
      </aside>

      {/* Mobile drawer */}
      {mobileOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <button
            type="button"
            aria-label="Close menu"
            onClick={() => setMobileOpen(false)}
            className="absolute inset-0 bg-black/40 backdrop-blur-[2px]"
          />
          <aside className="animate-slide-in absolute inset-y-0 left-0 w-72 border-r border-border-base bg-surface">
            {sidebar}
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex items-center gap-3 border-b border-border-base bg-bg/80 px-4 py-3 backdrop-blur-md lg:hidden">
          <button
            type="button"
            onClick={() => setMobileOpen(true)}
            className="rounded-md p-1.5 text-fg-muted hover:bg-surface-2 hover:text-fg"
            aria-label="Open menu"
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden>
              <path d="M4 7h16M4 12h16M4 17h16" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
          </button>
          <span className="text-sm font-semibold tracking-tight">Groundwork</span>
        </header>

        <main className="min-w-0 flex-1">{children}</main>
      </div>
    </div>
  );
}

/**
 * Per-link pending state, so the item you clicked is the thing that shows
 * progress — a page-level spinner alone can't say *which* page is coming.
 *
 * `useLinkStatus` only works inside a `<Link>`, which is why this is its own
 * component. The 14px slot is always rendered and only its contents change:
 * the Next docs call out inline indicators as a layout-shift risk, and a nav
 * row that widens on click would be exactly that. The spinner itself is
 * conditional rather than hidden, so its screen-reader label doesn't sit in
 * the accessibility tree announcing "Loading" on all five items at rest.
 */
function NavPending() {
  const { pending } = useLinkStatus();

  return (
    <span className="ml-auto grid size-3.5 shrink-0 place-items-center">
      {pending && <Spinner size={13} label="Loading page" className="text-fg-subtle" />}
    </span>
  );
}

const iconProps = {
  width: 15,
  height: 15,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.8,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  'aria-hidden': true,
};

function GridIcon() {
  return (
    <svg {...iconProps}>
      <rect x="3" y="3" width="7" height="7" rx="1.5" />
      <rect x="14" y="3" width="7" height="7" rx="1.5" />
      <rect x="3" y="14" width="7" height="7" rx="1.5" />
      <rect x="14" y="14" width="7" height="7" rx="1.5" />
    </svg>
  );
}

function ChatIcon() {
  return (
    <svg {...iconProps}>
      <path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 8.9 8.9 0 0 1-3.8-.9L3 20.5l1.6-4.8A8.4 8.4 0 0 1 12 3.1a8.4 8.4 0 0 1 9 8.4z" />
    </svg>
  );
}

function DocIcon() {
  return (
    <svg {...iconProps}>
      <path d="M14 2H6.5A1.5 1.5 0 0 0 5 3.5v17A1.5 1.5 0 0 0 6.5 22h11a1.5 1.5 0 0 0 1.5-1.5V7z" />
      <path d="M14 2v5h5M9 13h6M9 17h4" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg {...iconProps}>
      <path d="M9 11l3 3L22 4" />
      <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
    </svg>
  );
}

function PulseIcon() {
  return (
    <svg {...iconProps}>
      <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
    </svg>
  );
}

function ChartIcon() {
  return (
    <svg {...iconProps}>
      <path d="M3 3v16a2 2 0 0 0 2 2h16" />
      <path d="M7 15l4-5 3 3 5-6" />
    </svg>
  );
}
