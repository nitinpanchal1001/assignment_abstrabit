import type { ReactNode } from 'react';

/** Shared presentational primitives, so pages stay about data rather than markup. */

/**
 * The one loading indicator in the app.
 *
 * A track ring plus a rotating arc, drawn in `currentColor` so it inherits the
 * colour of whatever it sits in — a button, a nav item, a page. `role="status"`
 * with a visually hidden label is what makes it announce to a screen reader;
 * a bare spinning graphic is silent, so those users get no feedback at all
 * that anything is happening.
 */
export function Spinner({
  size = 16,
  label = 'Loading',
  className = '',
}: {
  size?: number;
  /** Announced to assistive tech. Say what is loading where it isn't obvious. */
  label?: string;
  className?: string;
}) {
  return (
    <span role="status" className={`inline-flex shrink-0 ${className}`}>
      <svg
        width={size}
        height={size}
        viewBox="0 0 24 24"
        fill="none"
        className="animate-spin"
        aria-hidden
      >
        <circle cx="12" cy="12" r="9.5" stroke="currentColor" strokeWidth="2.4" opacity="0.18" />
        {/* A quarter-turn arc: enough of the ring to read as rotation at 14px. */}
        <path
          d="M21.5 12a9.5 9.5 0 0 0-9.5-9.5"
          stroke="currentColor"
          strokeWidth="2.4"
          strokeLinecap="round"
        />
      </svg>
      <span className="sr-only">{label}</span>
    </span>
  );
}

/**
 * Route-level loading UI: a centred spinner that fades in only if the wait is
 * long enough to notice.
 *
 * The delay is the point. Most navigations resolve in well under 200ms, and an
 * indicator that appears and vanishes inside that window reads as a flicker —
 * it makes the app feel less stable, not more responsive. See
 * `.fade-in-delayed` in globals.css.
 */
export function LoadingBlock({
  label = 'Loading',
  className = 'min-h-[60vh]',
}: {
  label?: string;
  className?: string;
}) {
  return (
    <div className={`fade-in-delayed flex flex-col items-center justify-center gap-3 ${className}`}>
      <Spinner size={22} label={label} className="text-brand" />
      <p className="text-xs text-fg-subtle">{label}…</p>
    </div>
  );
}

export function PageHeader({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-4 border-b border-border-base px-6 py-5">
      <div className="min-w-0">
        <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 text-sm text-fg-muted">{description}</p>}
      </div>
      {action}
    </header>
  );
}

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-lg border border-border-base bg-surface ${className}`}>{children}</div>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="rounded-lg border border-dashed border-border-strong px-6 py-12 text-center">
      <p className="text-sm font-medium">{title}</p>
      <p className="mx-auto mt-1.5 max-w-md text-sm text-fg-muted">{description}</p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

const TONES = {
  neutral: 'bg-surface-2 text-fg-muted',
  accent: 'bg-brand-soft text-brand',
  success: 'bg-success-soft text-success',
  warning: 'bg-warning-soft text-warning',
  danger: 'bg-danger-soft text-danger',
} as const;

export function Badge({
  children,
  tone = 'neutral',
}: {
  children: ReactNode;
  tone?: keyof typeof TONES;
}) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${TONES[tone]}`}
    >
      {children}
    </span>
  );
}

export function StatTile({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <Card className="p-4">
      <p className="text-[11px] font-semibold uppercase tracking-wider text-fg-subtle">{label}</p>
      <p className="mt-1.5 text-2xl font-semibold tabular-nums">{value}</p>
      {hint && <p className="mt-0.5 text-xs text-fg-muted">{hint}</p>}
    </Card>
  );
}

export function formatDate(value: string | Date): string {
  return new Date(value).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
