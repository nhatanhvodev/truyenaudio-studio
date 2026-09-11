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
  root.style.fontSize = ROOT_FONT_SIZE[preferences.fontScale];
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
