import { NextResponse, type NextRequest } from 'next/server';

/** Set by the FastAPI backend; httpOnly, so only the server ever reads it. */
const SESSION_COOKIE = 'groundwork_session';

/**
 * Cheap redirect gate for the dashboard.
 *
 * This checks only that a session cookie is PRESENT — it does not verify the
 * signature. That is deliberate: verifying here would mean shipping
 * AUTH_SECRET to the frontend deployment as well as the API, duplicating the
 * one secret that must not be duplicated, purely to decide a redirect.
 *
 * It is therefore explicitly not the security boundary. Every dashboard page
 * fetches from the API with the cookie attached, and the API verifies the JWT
 * and re-checks workspace membership before returning a byte of tenant data. A
 * forged cookie gets past this redirect and then receives 401 from the API,
 * which the page turns into a redirect to /login.
 *
 * Named `proxy`, in `proxy.ts`: Next.js 16 deprecated the `middleware`
 * filename and named export.
 */
export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const hasSession = Boolean(request.cookies.get(SESSION_COOKIE)?.value);

  if (!hasSession && pathname.startsWith('/dashboard')) {
    const url = request.nextUrl.clone();
    url.pathname = '/login';
    url.searchParams.set('next', pathname);
    return NextResponse.redirect(url);
  }

  if (hasSession && pathname === '/login') {
    const url = request.nextUrl.clone();
    url.pathname = '/dashboard';
    url.search = '';
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    /*
     * Everything except static assets, image files, and /api/* — the latter is
     * rewritten straight to the backend and must not be intercepted.
     */
    '/((?!api|_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)',
  ],
};
