import { describe, expect, it } from 'vitest';

import {
  defaultPreferences,
  parsePreferences,
  preferencesAreSafe,
  serializePreferences,
} from './uiPreferences';

describe('UI preferences (U10)', () => {
  it('round-trips only the known presentation keys', () => {
    const preferences = { ...defaultPreferences(), theme: 'dark' as const, density: 'compact' as const };

    const raw = serializePreferences(preferences);

    expect(JSON.parse(raw)).toEqual({
      version: 1,
      theme: 'dark',
      density: 'compact',
      fontScale: 'medium',
      reduceMotion: false,
    });
    expect(parsePreferences(raw).preferences).toEqual(preferences);
  });

  it('drops unknown keys instead of echoing them back', () => {
    const result = parsePreferences(
      JSON.stringify({ version: 1, theme: 'light', apiKey: 'sk-secret-value', note: 'nội dung truyện' }),
    );

    expect(result.preferences.theme).toBe('light');
    expect(result.rejectedKeys.sort()).toEqual(['apiKey', 'note']);
    expect(result.migrated).toBe(true);
    expect(serializePreferences(result.preferences)).not.toContain('sk-secret-value');
    expect(serializePreferences(result.preferences)).not.toContain('nội dung truyện');
  });

  it('never persists text or secret-looking values through the appearance store', () => {
    const raw = JSON.stringify({ version: 1, theme: 'dark', styleGuide: 'Dịch giọng cổ trang' });

    const result = parsePreferences(raw);

    expect(result.rejectedKeys).toEqual(['styleGuide']);
    expect(serializePreferences(result.preferences)).toBe(
      JSON.stringify({ version: 1, theme: 'dark', density: 'comfortable', fontScale: 'medium', reduceMotion: false }),
    );
    expect(preferencesAreSafe(raw)).toBe(false);
    expect(preferencesAreSafe(serializePreferences(result.preferences))).toBe(true);
  });

  it('falls back to defaults for invalid values and reports the migration', () => {
    const result = parsePreferences(
      JSON.stringify({ version: 0, theme: 'neon', density: 42, fontScale: 'huge', reduceMotion: 'yes' }),
    );

    expect(result.preferences).toEqual(defaultPreferences());
    expect(result.migrated).toBe(true);
  });

  it('treats missing, empty and corrupt payloads as the default without failing', () => {
    expect(parsePreferences(null)).toEqual({ preferences: defaultPreferences(), rejectedKeys: [], migrated: false });
    expect(parsePreferences('')).toEqual({ preferences: defaultPreferences(), rejectedKeys: [], migrated: false });
    expect(parsePreferences('{not json').migrated).toBe(true);
    expect(parsePreferences('[]').migrated).toBe(true);
    expect(parsePreferences('"dark"').migrated).toBe(true);
  });

  it('accepts a boolean reduceMotion and keeps it in the saved document', () => {
    const result = parsePreferences(JSON.stringify({ version: 1, reduceMotion: true }));

    expect(result.preferences.reduceMotion).toBe(true);
    expect(result.migrated).toBe(false);
    expect(JSON.parse(serializePreferences(result.preferences)).reduceMotion).toBe(true);
  });

  it('reports safety for empty and valid documents', () => {
    expect(preferencesAreSafe(null)).toBe(true);
    expect(preferencesAreSafe('')).toBe(true);
    expect(preferencesAreSafe('{not json')).toBe(false);
    expect(preferencesAreSafe(JSON.stringify({ version: 1, theme: 'dark' }))).toBe(true);
  });
});
