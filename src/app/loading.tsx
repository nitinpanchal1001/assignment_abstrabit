import { Spinner } from '@/components/ui';

/**
 * Root fallback — the only one that covers `dashboard/layout.tsx`.
 *
 * A `loading.tsx` wraps the page and nested layouts of its segment, but never
 * the layout beside it. The dashboard layout fetches the session on every cold
 * entry, so without this file that request blocks navigation with nothing on
 * screen. Verified against `node_modules/next/dist/docs/.../loading.md`:
 * "It does not wrap the layout.js ... in the same segment."
 */
export default function Loading() {
  return (
    <div className="fade-in-delayed grid min-h-screen place-items-center">
      <div className="flex flex-col items-center gap-4">
        <span className="grid size-9 place-items-center rounded-xl bg-primary text-primary-fg">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden>
            <path d="M4 7h16M4 12h10M4 17h7" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
          </svg>
        </span>
        <Spinner size={18} label="Loading Groundwork" className="text-fg-subtle" />
      </div>
    </div>
  );
}
