/**
 * U10: harmless UI preferences (theme/font/density) for the browser.
 *
 * Only presentation values live here — no project data, no text content and no
 * credentials — because the UI preference store is the one place the browser is
 * allowed to keep state (plan C07: project data stays on the server).
 *
 * The parser is deliberately strict and *lossy on purpose*:
 * - unknown keys are dropped from `preferences` and their NAMES are reported to
 *   the caller in `rejectedKeys`. The parser reports names because a caller may
 *   need to log or assert on them; it never reports a VALUE from a rejected key.
 * - keys that look like content or secrets (`text`, `content`, `prompt`,
 *   `styleGuide`, `apiKey`, `token`, `secret`, …) are dropped even if their value
 *   is a plain string, so a pasted draft or a credential can never be persisted
 *   through the appearance settings;
 * - invalid values fall back to the default and are reported;
 * - `serializePreferences` writes only the known keys, so the stored document
 *   can never contain anything else.
 *
 * `rejectedKeys` holds key NAMES read straight out of the stored JSON, so they
 * are attacker-influenceable strings: anyone who can write to this origin's
 * localStorage chooses them, and a stored document may carry any number of them.
 * They are therefore kept OUT of the DOM. `AppearanceSettings` renders the count
 * and nothing else. This paragraph used to claim the keys were "never echoed
 * back" while the settings screen listed every one of them by name - the code
 * did not do what the comment said, and the test that covered it could not see
 * the difference (`AppearanceSettings.test.tsx` checked the values, and only the
 * values, never appeared). The count is what makes the claim true.
 */

export const UI_PREFERENCES_VERSION = 1;

export const THEMES = ['light', 'dark', 'system'] as const;
export const DENSITIES = ['compact', 'comfortable'] as const;
export const FONT_SCALES = ['small', 'medium', 'large'] as const;

export type Theme = (typeof THEMES)[number];
export type Density = (typeof DENSITIES)[number];
export type FontScale = (typeof FONT_SCALES)[number];

export type UiPreferences = {
  version: number;
  theme: Theme;
  density: Density;
  fontScale: FontScale;
  reduceMotion: boolean;
};

export type ParseResult = {
  preferences: UiPreferences;
  /** Keys that were present but not accepted (never returned in `preferences`). */
  rejectedKeys: string[];
  /** True when something had to be dropped or replaced by a default. */
  migrated: boolean;
};

const CONTENT_OR_SECRET_PATTERN =
  /(text|content|prompt|style|guide|glossary|memory|draft|api[_-]?key|key|token|secret|password|credential|authorization|bearer)/i;

export function defaultPreferences(): UiPreferences {
  return {
    version: UI_PREFERENCES_VERSION,
    theme: 'dark',
    density: 'comfortable',
    fontScale: 'medium',
    reduceMotion: false,
  };
}

export function parsePreferences(raw: string | null | undefined): ParseResult {
  const fallback = defaultPreferences();
  if (raw === null || raw === undefined || raw.trim() === '') {
    return { preferences: fallback, rejectedKeys: [], migrated: false };
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { preferences: fallback, rejectedKeys: [], migrated: true };
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    return { preferences: fallback, rejectedKeys: [], migrated: true };
  }

  const source = parsed as Record<string, unknown>;
  const rejectedKeys: string[] = [];
  let migrated = false;

  const known = new Set(Object.keys(fallback));
  for (const key of Object.keys(source)) {
    if (!known.has(key)) {
      rejectedKeys.push(key);
      migrated = true;
    }
  }

  const preferences: UiPreferences = { ...fallback };
  preferences.theme = pickUnion(source.theme, THEMES, fallback.theme, () => (migrated = true));
  preferences.density = pickUnion(source.density, DENSITIES, fallback.density, () => (migrated = true));
  preferences.fontScale = pickUnion(source.fontScale, FONT_SCALES, fallback.fontScale, () => (migrated = true));
  if (typeof source.reduceMotion === 'boolean') {
    preferences.reduceMotion = source.reduceMotion;
  } else if (source.reduceMotion !== undefined) {
    migrated = true;
  }
  if (source.version !== undefined && source.version !== UI_PREFERENCES_VERSION) {
    // An older/newer preference document keeps its presentation values but is
    // re-versioned on the next save.
    migrated = true;
  }

  return { preferences, rejectedKeys, migrated };
}

export function serializePreferences(preferences: UiPreferences): string {
  // Explicit key list: nothing outside UiPreferences can ever be written.
  return JSON.stringify({
    version: UI_PREFERENCES_VERSION,
    theme: preferences.theme,
    density: preferences.density,
    fontScale: preferences.fontScale,
    reduceMotion: preferences.reduceMotion === true,
  });
}

/**
 * True when every stored key is a known presentation key with a harmless value.
 * Used by the settings screen before a preference is saved.
 */
export function preferencesAreSafe(raw: string | null | undefined): boolean {
  if (raw === null || raw === undefined || raw.trim() === '') {
    return true;
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return false;
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    return false;
  }
  const known = new Set(Object.keys(defaultPreferences()));
  for (const [key, value] of Object.entries(parsed as Record<string, unknown>)) {
    if (!known.has(key)) {
      return false;
    }
    if (typeof value === 'string' && CONTENT_OR_SECRET_PATTERN.test(value)) {
      return false;
    }
  }
  return true;
}

function pickUnion<T extends string>(
  value: unknown,
  allowed: readonly T[],
  fallback: T,
  onInvalid: () => void,
): T {
  if (value === undefined) {
    return fallback;
  }
  if (typeof value === 'string' && (allowed as readonly string[]).includes(value)) {
    return value as T;
  }
  onInvalid();
  return fallback;
}
