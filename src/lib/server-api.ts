import 'server-only';

import { cookies } from 'next/headers';

import type { ApiErrorBody } from './types';

/**
 * Server-side calls to the FastAPI backend.
 *
 * Server Components talk to the API directly rather than through the Next
 * rewrite: the rewrite exists to give the *browser* a single origin, and
 * routing a server-to-server call back through the public URL would add a
 * pointless hop.
 *
 * The session cookie must be forwarded explicitly — a Server Component's fetch
 * carries no browser cookies of its own, so without this every request would
 * arrive unauthenticated.
 */
// `?.trim() ||` rather than `??`: an empty BACKEND_URL= line in .env.local
// would otherwise make every server-side call target a relative path.
const BACKEND_URL = process.env.BACKEND_URL?.trim() || 'http://127.0.0.1:8000';

export class ServerApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly requestId?: string,
  ) {
    super(message);
    this.name = 'ServerApiError';
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const cookieStore = await cookies();
  const cookieHeader = cookieStore
    .getAll()
    .map((c) => `${c.name}=${c.value}`)
    .join('; ');

  let response: Response;
  try {
    response = await fetch(`${BACKEND_URL}${path}`, {
      ...init,
      headers: { ...init?.headers, cookie: cookieHeader },
      // Dashboard data is per-user and changes constantly; caching it would
      // serve one tenant's view to another.
      cache: 'no-store',
    });
  } catch {
    throw new ServerApiError(
      503,
      'network/unreachable',
      'The API service is unreachable. It may still be starting up.',
    );
  }

  if (response.status === 204) return undefined as T;

  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as ApiErrorBody | null;
    throw new ServerApiError(
      response.status,
      body?.error?.code ?? 'internal',
      body?.error?.message ?? 'That request could not be completed.',
      body?.error?.requestId,
    );
  }

  return (await response.json()) as T;
}

export const serverApi = {
  get: <T>(path: string) => request<T>(path),
};

/**
 * Like ``get`` but returns null on 401/404 instead of throwing.
 *
 * Used where "not signed in" or "no such workspace" is an expected branch the
 * page handles by redirecting, not an error worth an error boundary.
 */
export async function getOrNull<T>(path: string): Promise<T | null> {
  try {
    return await serverApi.get<T>(path);
  } catch (error) {
    if (error instanceof ServerApiError && (error.status === 401 || error.status === 404)) {
      return null;
    }
    throw error;
  }
}
