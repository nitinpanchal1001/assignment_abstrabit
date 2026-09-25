import Link from 'next/link';

export default function NotFound() {
  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-6 py-16">
      <p className="text-[11px] font-medium uppercase tracking-[0.2em] text-brand">404</p>
      <h1 className="mt-3 text-2xl font-semibold tracking-tight">Page not found</h1>
      <p className="mt-2 text-sm leading-relaxed text-fg-muted">
        That URL doesn&rsquo;t exist. If you followed a link to a workspace or document, it may have
        been deleted, or belong to an account you&rsquo;re not signed in to.
      </p>
      <div className="mt-6">
        <Link
          href="/dashboard"
          className="inline-flex items-center rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-fg transition-colors hover:bg-primary-hover"
        >
          Back to dashboard
        </Link>
      </div>
    </main>
  );
}
