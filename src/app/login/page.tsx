import { Suspense } from 'react';

import { LoginForm } from '@/components/login-form';
import { ThemeToggle } from '@/components/theme-toggle';
import { LoadingBlock } from '@/components/ui';

export const dynamic = 'force-dynamic';

export default function LoginPage() {
  return (
    <main className="relative mx-auto flex min-h-screen w-full max-w-sm flex-col justify-center px-6 py-16">
      <div className="absolute right-6 top-6 w-28">
        <ThemeToggle />
      </div>

      <div className="mb-8">
        <p className="text-[11px] font-medium uppercase tracking-[0.2em] text-brand">Groundwork</p>
        <h1 className="mt-3 text-2xl font-semibold tracking-tight">Sign in</h1>
        <p className="mt-2 text-sm leading-relaxed text-fg-muted">
          Use the demo account from the README, or create an account to get your own workspace.
        </p>
      </div>

      {/* The form reads `?next=` from the query string, which suspends until
          the client has it. Reserving the form's height keeps the heading
          above from jumping when it resolves. */}
      <Suspense fallback={<LoadingBlock label="Loading sign-in" className="h-72" />}>
        <LoginForm />
      </Suspense>
    </main>
  );
}
