import type { Metadata } from 'next';
import Script from 'next/script';

import './globals.css';

export const metadata: Metadata = {
  title: 'Groundwork — workspace-scoped document assistant',
  description:
    'Ask questions grounded in your workspace documents, with citations, tool calling, and strict tenant isolation.',
};

/**
 * Applies the stored theme before first paint.
 *
 * This must run synchronously, ahead of rendering: applying the theme in an
 * effect gives every dark-mode user a white flash on load. `beforeInteractive`
 * is the supported way to do that in the App Router — a bare <script> element
 * inside a component works during SSR but makes React warn, because it would
 * silently not execute on a client render.
 *
 * Wrapped in try/catch because localStorage throws outright in some privacy
 * modes, and a theme preference must never be able to break the page.
 */
const themeScript = `(function(){try{var t=localStorage.getItem('groundwork-theme');if(t==='dark'||t==='light'){document.documentElement.setAttribute('data-theme',t);}}catch(e){}})();`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // suppressHydrationWarning: the script above mutates <html> before React
    // hydrates, so the server and client markup legitimately differ here.
    <html lang="en" suppressHydrationWarning>
      <body className="min-h-screen antialiased">
        <Script
          id="theme-init"
          strategy="beforeInteractive"
          dangerouslySetInnerHTML={{ __html: themeScript }}
        />
        {children}
      </body>
    </html>
  );
}
