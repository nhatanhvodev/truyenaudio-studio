import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  UI_PREFERENCES_STORAGE_KEY,
  applyPreferences,
  notifyPreferencesChanged,
  readStoredPreferences,
  resolveTheme,
  subscribePreferences,
} from './themeRuntime';
import { defaultPreferences, parsePreferences } from './uiPreferences';

describe('resolveTheme', () => {
  it('returns the pinned theme unchanged', () => {
    expect(resolveTheme('light', true)).toBe('light');
    expect(resolveTheme('dark', false)).toBe('dark');
  });

  it('follows the system preference only for "system"', () => {
    expect(resolveTheme('system', true)).toBe('dark');
    expect(resolveTheme('system', false)).toBe('light');
  });
});

describe('applyPreferences', () => {
  beforeEach(() => {
    document.documentElement.removeAttribute('data-theme');
    document.documentElement.removeAttribute('data-density');
    document.documentElement.style.fontSize = '';
  });

  it('writes data-theme, data-density and the root font size', () => {
    applyPreferences({ ...defaultPreferences(), theme: 'light', density: 'compact', fontScale: 'large' });
    expect(document.documentElement.dataset.theme).toBe('light');
    expect(document.documentElement.dataset.density).toBe('compact');
    expect(document.documentElement.style.fontSize).toBe('112.5%');
  });

  it('maps each font scale to a distinct root size', () => {
    const seen = new Set<string>();
    for (const fontScale of ['small', 'medium', 'large'] as const) {
      applyPreferences({ ...defaultPreferences(), fontScale });
      seen.add(document.documentElement.style.fontSize);
    }
    expect(seen.size).toBe(3);
  });
});

describe('readStoredPreferences', () => {
  afterEach(() => window.localStorage.clear());

  it('falls back to the defaults when nothing is stored', () => {
    expect(readStoredPreferences()).toEqual(defaultPreferences());
  });

  it('reads a stored document back', () => {
    window.localStorage.setItem(UI_PREFERENCES_STORAGE_KEY, JSON.stringify({ ...defaultPreferences(), theme: 'light' }));
    expect(readStoredPreferences().theme).toBe('light');
  });
});

describe('subscribePreferences', () => {
  afterEach(() => window.localStorage.clear());

  it('notifies listeners on the same tab', () => {
    const listener = vi.fn();
    const unsubscribe = subscribePreferences(listener);
    notifyPreferencesChanged();
    expect(listener).toHaveBeenCalledTimes(1);
    unsubscribe();
    notifyPreferencesChanged();
    expect(listener).toHaveBeenCalledTimes(1);
  });
});

describe('boot script parity with resolveTheme', () => {
  // Bind the base first — see the note in tokens-contrast.test.ts.
  const here = import.meta.url;
  const html = readFileSync(fileURLToPath(new URL('../../../index.html', here)), 'utf8');
  const body = /<script>\s*([\s\S]*?)<\/script>/.exec(html)?.[1];

  interface BootResult {
    theme: string;
    density: string;
    fontSize: string;
  }

  function runBootScript(storedRaw: string | null, systemDark: boolean): BootResult {
    if (!body) throw new Error('boot script not found in index.html');
    const root = { dataset: {} as Record<string, string>, style: { fontSize: '' } };
    const window = {
      localStorage: { getItem: () => storedRaw },
      matchMedia: () => ({ matches: systemDark }),
    };
    new Function('window', 'document', body)(window, { documentElement: root });
    return { theme: root.dataset.theme, density: root.dataset.density, fontSize: root.style.fontSize };
  }

  function stored(theme: string | null, extra: Record<string, string> = {}): string | null {
    return theme === null ? null : JSON.stringify({ version: 1, theme, ...extra });
  }

  // No stored preference resolves to 'dark' on BOTH system settings, because
  // dark is what defaultPreferences() returns — Task 4 made that true. Only an
  // explicit 'system' follows the OS. This is the exact drift this test exists
  // to catch: the boot script carries its own copy of the normalise-then-resolve
  // logic, and the day the two copies disagree, the app paints one theme and
  // snaps to the other the moment React mounts.
  it.each([
    ['light', true, 'light'],
    ['dark', false, 'dark'],
    ['system', true, 'dark'],
    ['system', false, 'light'],
    [null, true, 'dark'],
    [null, false, 'dark'],
    ['garbage', true, 'dark'],
    ['garbage', false, 'dark'],
  ])('stored=%s systemDark=%s -> %s', (theme, systemDark, expected) => {
    expect(runBootScript(stored(theme as string | null), systemDark as boolean).theme).toBe(expected);

    // The app-side path, fed the SAME inputs. Parse the stored document the way
    // the app does, then resolve. Do NOT shortcut this to
    // `resolveTheme(theme ?? 'dark', …)`: that hands resolveTheme a value the
    // app never produces, so the assertion would pass even while the two
    // implementations disagreed — which is the one thing this test exists to
    // prevent. parsePreferences is also what turns a stored 'garbage' theme
    // into the dark default, and what makes the null case match the boot
    // script rather than following the OS.
    const parsed = parsePreferences(stored(theme as string | null)).preferences;
    expect(resolveTheme(parsed.theme, systemDark as boolean)).toBe(expected);
  });

  it('writes density and root font size, defaulting when absent or invalid', () => {
    expect(runBootScript(null, true)).toMatchObject({ density: 'comfortable', fontSize: '100%' });
    expect(runBootScript(stored('dark', { density: 'compact', fontScale: 'large' }), true)).toMatchObject({
      density: 'compact',
      fontSize: '112.5%',
    });
    expect(runBootScript(stored('dark', { density: 'bogus', fontScale: 'bogus' }), true)).toMatchObject({
      density: 'comfortable',
      fontSize: '100%',
    });
  });

  it('keeps the dark default when localStorage throws', () => {
    if (!body) throw new Error('boot script not found in index.html');
    const root = { dataset: {} as Record<string, string>, style: { fontSize: '' } };
    const window = {
      localStorage: {
        getItem: () => {
          throw new Error('storage blocked');
        },
      },
      matchMedia: () => ({ matches: false }),
    };
    new Function('window', 'document', body)(window, { documentElement: root });
    expect(root.dataset.theme).toBe('dark');
  });
});
