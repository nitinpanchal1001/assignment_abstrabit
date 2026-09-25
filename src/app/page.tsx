import Link from 'next/link';
import { redirect } from 'next/navigation';

import { ThemeToggle } from '@/components/theme-toggle';
import { getOrNull } from '@/lib/server-api';
import type { SessionPayload } from '@/lib/types';

export const dynamic = 'force-dynamic';
// See dashboard/layout.tsx: Render cold start vs the Hobby function timeout.
export const maxDuration = 60;


const FEATURES = [
  ['Grounded retrieval', 'Hybrid vector + keyword search, cited back to file and section.'],
  ['Honest refusal', "Says it doesn't know rather than inventing an answer."],
  ['Tool calling', 'Saves tasks and posts summaries. Every call validated and logged.'],
  ['Tenant isolation', 'One shared vector store; the workspace filter is never optional.'],
] as const;

export default async function HomePage() {
  const session = await getOrNull<SessionPayload>('/api/auth/session');
  if (session) redirect('/dashboard');

  return (
    <main className="relative mx-auto flex min-h-screen max-w-2xl flex-col justify-center px-6 py-20">
      <div className="absolute right-6 top-6 w-28">
        <ThemeToggle />
      </div>

      <p className="text-[11px] font-medium uppercase tracking-[0.2em] text-brand">Groundwork</p>

      <h1 className="mt-4 text-[2.5rem] font-semibold leading-[1.1] tracking-tight">
        Answers grounded in your documents — and nobody else&rsquo;s.
      </h1>

      <p className="mt-5 max-w-xl text-[15px] leading-relaxed text-fg-muted">
        Upload documents into a workspace, ask questions, and get answers cited to the source. Every
        workspace stays strictly separate, even though all of them share a single vector store.
      </p>

      {/* gap-px over a border-coloured background gives hairline dividers
          without doubling borders between adjacent cells. */}
      <ul className="mt-10 grid gap-px overflow-hidden rounded-xl border border-border-base bg-border-base sm:grid-cols-2">
        {FEATURES.map(([title, body]) => (
          <li key={title} className="bg-surface p-4">
            <p className="text-sm font-medium">{title}</p>
            <p className="mt-1 text-[13px] leading-relaxed text-fg-muted">{body}</p>
          </li>
        ))}
      </ul>

      <div className="mt-10">
        <Link
          href="/login"
          className="inline-flex items-center rounded-lg bg-primary px-5 py-2.5 text-sm font-medium text-primary-fg transition-colors hover:bg-primary-hover"
        >
          Sign in to continue
        </Link>
      </div>
    </main>
  );
}
