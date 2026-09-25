import type { NextConfig } from 'next';

/**
 * The FastAPI service. Server-side only — the browser never sees this URL.
 *
 * An empty value is treated as unset, not as an empty destination. `??` alone
 * falls back on undefined but not on '', so a `BACKEND_URL=` line left blank in
 * .env.local would rewrite /api/* to a relative path and every request would
 * 404 against the Next server itself.
 */
const BACKEND_URL = process.env.BACKEND_URL?.trim() || 'http://127.0.0.1:8000';

const nextConfig: NextConfig = {
  /**
   * Proxy /api/* to FastAPI so the browser only ever talks to ONE origin.
   *
   * This is a security decision, not a convenience. With the API on a separate
   * origin, the session cookie would have to be SameSite=None; Secure to be
   * sent at all — which removes the CSRF protection SameSite exists to give,
   * and is increasingly blocked by default as a third-party cookie. Proxying
   * keeps the cookie first-party and SameSite=Lax, and removes CORS entirely.
   *
   * The cost is one extra network hop between Vercel and the API host.
   */
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: `${BACKEND_URL}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
