import { describe, expect, it } from 'vitest';

import { colors, type UiTheme } from './tokens';

function luminance(hex: string): number {
  const raw = hex.replace('#', '');
  const channels = [0, 2, 4].map((offset) => parseInt(raw.slice(offset, offset + 2), 16) / 255);
  const linear = channels.map((channel) =>
    channel <= 0.03928 ? channel / 12.92 : Math.pow((channel + 0.055) / 1.055, 2.4),
  );
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

describe('design token contrast (U01)', () => {
  const themes: UiTheme[] = ['light', 'dark'];

  for (const theme of themes) {
    it(`${theme}: body text >= 4.5:1 and UI elements >= 3:1`, () => {
      const c = colors[theme];
      expect(contrast(c.text, c.surface)).toBeGreaterThanOrEqual(4.5);
      expect(contrast(c.text, c.surfaceRaised)).toBeGreaterThanOrEqual(4.5);
      expect(contrast(c.primary, c.textOnPrimary)).toBeGreaterThanOrEqual(4.5);
      expect(contrast(c.danger, c.surface)).toBeGreaterThanOrEqual(3);
      expect(contrast(c.focusRing, c.surface)).toBeGreaterThanOrEqual(3);
    });
  }
});
