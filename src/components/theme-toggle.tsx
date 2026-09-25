'use client';

import { useSyncExternalStore } from 'react';

export const THEME_STORAGE_KEY = 'groundwork-theme';

/** Fired on the same tab, since `storage` events only reach *other* tabs. */
const THEME_CHANGE_EVENT = 'groundwork:theme';

type Theme = 'light' | 'system' | 'dark';

/**
 * Three-state theme control.
 *
 * "System" is a real option rather than an implicit default: someone whose OS
 * switches at dusk wants the app to follow, and someone who has pinned a
 * theme wants it pinned. Storing only light/dark cannot express the first.
 *
 * The choice is written to `data-theme` on <html>, which globals.css gives
 * precedence over `prefers-color-scheme`. Choosing "system" removes the
 * attribute so the media query takes over again.
 *
 * Read through useSyncExternalStore rather than an effect-plus-state pair:
 * localStorage genuinely is an external store, and this is the primitive
 * built for it. It also gets the server snapshot right during hydration, and
 * keeps multiple tabs in sync for free via the `storage` event.
 */
export function ThemeToggle() {
  const theme = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  function apply(next: Theme) {
    const root = document.documentElement;

    if (next === 'system') {
      root.removeAttribute('data-theme');
    } else {
      root.setAttribute('data-theme', next);
    }

    try {
      if (next === 'system') localStorage.removeItem(THEME_STORAGE_KEY);
      else localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // Private mode or blocked storage: the current page still honours the
      // choice, it just won't persist.
    }

    window.dispatchEvent(new Event(THEME_CHANGE_EVENT));
  }

  const options: Array<{ value: Theme; label: string; icon: React.ReactNode }> = [
    { value: 'light', label: 'Light', icon: <SunIcon /> },
    { value: 'system', label: 'System', icon: <SystemIcon /> },
    { value: 'dark', label: 'Dark', icon: <MoonIcon /> },
  ];

  return (
    <div
      role="radiogroup"
      aria-label="Colour theme"
      className="flex items-center gap-0.5 rounded-lg border border-border-base bg-surface-2 p-0.5"
    >
      {options.map((option) => {
        const active = theme === option.value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={active}
            aria-label={option.label}
            title={option.label}
            onClick={() => apply(option.value)}
            className={`flex flex-1 items-center justify-center rounded-md py-1.5 transition-colors ${
              active ? 'bg-surface text-fg shadow-sm' : 'text-fg-subtle hover:text-fg-muted'
            }`}
          >
            {option.icon}
          </button>
        );
      })}
    </div>
  );
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener('storage', onChange);
  window.addEventListener(THEME_CHANGE_EVENT, onChange);
  return () => {
    window.removeEventListener('storage', onChange);
    window.removeEventListener(THEME_CHANGE_EVENT, onChange);
  };
}

/** Returns a primitive, so React's identity check is a value comparison. */
function getSnapshot(): Theme {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    return stored === 'dark' || stored === 'light' ? stored : 'system';
  } catch {
    return 'system';
  }
}

/** The server cannot know the preference; the inline head script applies it. */
function getServerSnapshot(): Theme {
  return 'system';
}

const iconProps = {
  width: 14,
  height: 14,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 2,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  'aria-hidden': true,
};

function SunIcon() {
  return (
    <svg {...iconProps}>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41" />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg {...iconProps}>
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
    </svg>
  );
}

function SystemIcon() {
  return (
    <svg {...iconProps}>
      <rect x="2" y="3" width="20" height="14" rx="2" />
      <path d="M8 21h8M12 17v4" />
    </svg>
  );
}
