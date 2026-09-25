'use client';

import type { ApiErrorBody } from '@/lib/types';

/**
 * Client-side fetch wrapper.
 *
 * Exists so no component has to remember the three ways a request can fail —
 * the network never reached the server, the server returned an error envelope,
 * or the response was not JSON at all. Each produced a different ad-hoc
 * message before; now they all arrive as an `ApiError` with something specific
 * to display.
 */

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly requestId?: string;
  readonly details?: unknown;

  constructor(init: {
    message: string;
    code: string;
    status: number;
    requestId?: string;
    details?: unknown;
  }) {
    super(init.message);
    this.name = 'ApiError';
    this.code = init.code;
    this.status = init.status;
    this.requestId = init.requestId;
    this.details = init.details;
  }
}

export async function apiFetch<T>(url: string, init?: RequestInit): Promise<T> {
  let response: Response;

  try {
    response = await fetch(url, init);
  } catch {
    // fetch only rejects for transport-level problems — offline, DNS, CORS,
    // an aborted connection. The server was never reached, so there is no
    // envelope to read.
    throw new ApiError({
      message: "Couldn't reach the server. Check your connection and try again.",
      code: 'network/unreachable',
      status: 0,
    });
  }

  if (response.ok) {
    if (response.status === 204) return undefined as T;
    try {
      return (await response.json()) as T;
    } catch {
      throw new ApiError({
        message: 'The server sent a response we could not read.',
        code: 'network/bad-response',
        status: response.status,
      });
    }
  }

  const body = (await response.json().catch(() => null)) as ApiErrorBody | null;

  if (body?.error?.message) {
    throw new ApiError({
      message: body.error.message,
      code: body.error.code ?? 'internal',
      status: response.status,
      requestId: body.error.requestId,
      details: body.error.details,
    });
  }

  // No envelope: a proxy timeout, a platform-level 413, or an HTML error page.
  // Map the statuses a user can actually act on rather than showing a number.
  throw new ApiError({
    message: statusMessage(response.status),
    code: 'internal',
    status: response.status,
  });
}

function statusMessage(status: number): string {
  switch (status) {
    case 401:
      return 'Your session has expired. Please sign in again.';
    case 403:
      return "You don't have permission to do that.";
    case 404:
      return "That wasn't found. It may have been deleted.";
    case 413:
      return 'That file is too large to upload.';
    case 429:
      return 'Too many requests. Please wait a moment and try again.';
    case 502:
    case 503:
    case 504:
      return 'The service is temporarily unavailable. Please try again shortly.';
    default:
      return status >= 500
        ? 'Something went wrong on our end. Please try again.'
        : 'That request could not be completed.';
  }
}

/** Extracts a displayable message from anything caught in a handler. */
export function messageOf(error: unknown, fallback = 'Something went wrong.'): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}
