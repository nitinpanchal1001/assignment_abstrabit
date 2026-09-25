'use client';

import { useRouter, useSearchParams } from 'next/navigation';

import { apiFetch, messageOf } from '@/lib/client/api';
import { useState, useTransition } from 'react';

import { Spinner } from './ui';

type Mode = 'signin' | 'signup';

export function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [mode, setMode] = useState<Mode>('signin');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();
  const [submitting, setSubmitting] = useState(false);

  const nextPath = searchParams.get('next') ?? '/dashboard';

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);

    try {
      // The session cookie is set by the server and is httpOnly, so there is
      // no token for this component to hold onto.
      await apiFetch(mode === 'signin' ? '/api/auth/login' : '/api/auth/signup', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ email, password }),
      });

      // refresh() re-runs the server components so they see the new cookie.
      startTransition(() => {
        router.replace(nextPath);
        router.refresh();
      });
    } catch (caught) {
      setError(messageOf(caught, 'Could not sign in.'));
      setSubmitting(false);
    }
  }

  const busy = submitting || isPending;

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="flex rounded-md border border-border-base bg-surface-2 p-0.5 text-sm">
        {(['signin', 'signup'] as const).map((value) => (
          <button
            key={value}
            type="button"
            onClick={() => {
              setMode(value);
              setError(null);
            }}
            className={`flex-1 rounded px-3 py-1.5 font-medium transition-colors ${
              mode === value ? 'bg-surface text-fg shadow-sm' : 'text-fg-muted hover:text-fg'
            }`}
          >
            {value === 'signin' ? 'Sign in' : 'Create account'}
          </button>
        ))}
      </div>

      <div className="space-y-1.5">
        <label htmlFor="email" className="block text-sm font-medium">
          Email
        </label>
        <input
          id="email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="w-full rounded-md border border-border-base bg-surface px-3 py-2 text-sm outline-none transition-colors focus:border-brand"
          placeholder="you@example.com"
        />
      </div>

      <div className="space-y-1.5">
        <label htmlFor="password" className="block text-sm font-medium">
          Password
        </label>
        <input
          id="password"
          type="password"
          autoComplete={mode === 'signin' ? 'current-password' : 'new-password'}
          required
          minLength={8}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="w-full rounded-md border border-border-base bg-surface px-3 py-2 text-sm outline-none transition-colors focus:border-brand"
          placeholder={mode === 'signup' ? 'At least 8 characters' : '••••••••'}
        />
      </div>

      {error && (
        <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}

      <button
        type="submit"
        disabled={busy}
        className="flex w-full items-center justify-center gap-2 rounded-md bg-primary px-4 py-2.5 text-sm font-medium text-primary-fg transition-colors hover:bg-primary-hover disabled:opacity-60"
      >
        {busy && <Spinner size={15} label="Signing in" />}
        {busy
          ? mode === 'signin'
            ? 'Signing in…'
            : 'Creating account…'
          : mode === 'signin'
            ? 'Sign in'
            : 'Create account'}
      </button>
    </form>
  );
}
