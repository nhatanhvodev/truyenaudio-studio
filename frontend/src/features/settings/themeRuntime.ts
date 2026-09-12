import { parsePreferences, type Theme, type UiPreferences } from './uiPreferences';

export const UI_PREFERENCES_STORAGE_KEY = 'studio.ui-preferences';

/** Root font sizes that implement the fontScale preference. */
export const ROOT_FONT_SIZE: Record<UiPreferences['fontScale'], string> = {
  small: '87.5%',
  medium: '100%',
  large: '112.5%',
};

const CHANGE_EVENT = 'studio:ui-preferences';

export function resolveTheme(theme: Theme, systemPrefersDark: boolean): 'light' | 'dark' {
  if (theme === 'system') {
    return systemPrefersDark ? 'dark' : 'light';
  }
  return theme;
}

function systemPrefersDark(): boolean {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia('(prefers-color-scheme: dark)').matches
    : true;
}

export function readStoredPreferences(): UiPreferences {
  if (typeof window === 'undefined') return parsePreferences(null).preferences;
  return parsePreferences(window.localStorage.getItem(UI_PREFERENCES_STORAGE_KEY)).preferences;
}

/**
 * The single place the DOM learns about presentation preferences.
 * Colours resolve through data-theme; fontScale through the root font-size, so
 * every rem in the app scales and browser zoom still applies on top.
 */
export function applyPreferences(preferences: UiPreferences): void {
  if (typeof document === 'undefined') return;
  const root = document.documentElement;
  root.dataset.theme = resolveTheme(preferences.theme, systemPrefersDark());
  root.dataset.density = preferences.density;
  root.dataset.reduceMotion = preferences.reduceMotion ? 'true' : 'false';
  root.style.fontSize = ROOT_FONT_SIZE[preferences.fontScale];
}

/**
 * Set once the user has acknowledged the dark-default notice, so it is shown at
 * most once per browser.
 */
export const THEME_NOTICE_STORAGE_KEY = 'studio.ui-theme-notice';

/**
 * True when this browser still needs telling that the DEFAULT theme is dark.
 *
 * ADR-0002 makes dark the default, so a user who had never opened Appearance
 * settings now lands on a dark app even when their operating system is light.
 * That is the intended identity change — but it is also a surprise, and it is
 * worth being precise about who actually gets it:
 *
 * - anyone with a stored preference document keeps their value, INCLUDING an
 *   explicit "system", so nothing changed under them and they are not told;
 * - an OS-dark user saw dark before and sees dark now, so nothing changed for
 *   them either;
 * - only "no stored preference document AND OS light" is a real change.
 *
 * Returns false rather than throwing when storage is unavailable: a browser with
 * storage blocked must not get a notice it can never dismiss.
 */
export function shouldAnnounceDarkDefault(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    if (window.localStorage.getItem(THEME_NOTICE_STORAGE_KEY) !== null) return false;
    if (window.localStorage.getItem(UI_PREFERENCES_STORAGE_KEY) !== null) return false;
  } catch {
    return false;
  }
  return !systemPrefersDark();
}

export function acknowledgeDarkDefaultNotice(): void {
  try {
    window.localStorage.setItem(THEME_NOTICE_STORAGE_KEY, '1');
  } catch {
    // Storage blocked: the notice returns on the next load. Better than dropping
    // an acknowledgement that was never persisted.
  }
}

export function notifyPreferencesChanged(): void {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

export function subscribePreferences(listener: (preferences: UiPreferences) => void): () => void {
  if (typeof window === 'undefined') return () => {};
  const handler = () => listener(readStoredPreferences());
  window.addEventListener(CHANGE_EVENT, handler);
  window.addEventListener('storage', handler);
  return () => {
    window.removeEventListener(CHANGE_EVENT, handler);
    window.removeEventListener('storage', handler);
  };
}
