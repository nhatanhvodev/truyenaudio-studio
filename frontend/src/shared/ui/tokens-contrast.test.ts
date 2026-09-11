import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

// The base URL is bound to a variable first: Vite rewrites the literal form
// `new URL('...', import.meta.url)` into a dev-server asset URL (http://...),
// which `fileURLToPath` rejects. Through a variable it stays a file: URL.
const here = import.meta.url;
const CSS_PATH = fileURLToPath(new URL('../../styles/tokens.css', here));

/** Pull `--name: #value;` declarations out of one selector block. */
function readBlock(css: string, selector: string): Record<string, string> {
  const start = css.indexOf(selector);
  if (start === -1) throw new Error(`selector ${selector} not found in tokens.css`);
  const open = css.indexOf('{', start);
  const close = css.indexOf('}', open);
  const body = css.slice(open + 1, close);
  const out: Record<string, string> = {};
  for (const line of body.split('\n')) {
    const match = /^\s*(--[a-z-]+):\s*(#[0-9a-fA-F]{6})\s*;/.exec(line);
    if (match) out[match[1]] = match[2].toLowerCase();
  }
  return out;
}

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

/** Strip CSS comments so a selector lookup cannot match prose describing it. */
function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, '');
}

const css = stripComments(readFileSync(CSS_PATH, 'utf8'));

const SELECTORS = [
  { label: 'dark', selector: ':root' },
  { label: 'light', selector: "[data-theme='light']" },
];

/** [foreground, background, minimum, purpose] */
const PAIRS: Array<[string, string, number, string]> = [
  ['--text', '--surface', 4.5, 'body text'],
  ['--text', '--bg', 4.5, 'body text on background'],
  ['--text', '--surface-raised', 4.5, 'body text on raised surface'],
  ['--text-muted', '--surface', 4.5, 'muted text'],
  ['--text-muted', '--bg', 4.5, 'muted text on background'],
  ['--text-subtle', '--surface', 4.5, 'subtle text'],
  ['--on-primary', '--primary', 4.5, 'label on primary fill'],
  ['--on-primary', '--primary-hover', 4.5, 'label on primary hover fill'],
  ['--on-primary', '--danger', 4.5, 'label on danger fill'],
  ['--success', '--surface', 4.5, 'success as text'],
  ['--warning', '--surface', 4.5, 'warning as text'],
  ['--danger', '--surface', 4.5, 'danger as text'],
  ['--primary', '--surface', 3.0, 'accent as UI element'],
  ['--focus', '--surface', 3.0, 'focus ring'],
  ['--focus', '--surface-raised', 3.0, 'focus ring on raised surface'],
  ['--border-control', '--surface', 3.0, 'control outline'],
  ['--border-control', '--surface-raised', 3.0, 'control outline on raised surface'],
];

describe('design token contrast (U01, ADR-0002)', () => {
  for (const { label, selector } of SELECTORS) {
    describe(label, () => {
      const tokens = readBlock(css, selector);
      for (const [fg, bg, min, purpose] of PAIRS) {
        it(`${purpose}: ${fg} on ${bg} >= ${min}:1`, () => {
          const value = contrast(tokens[fg], tokens[bg]);
          expect(value, `${tokens[fg]} on ${tokens[bg]} = ${value.toFixed(2)}:1`).toBeGreaterThanOrEqual(min);
        });
      }
    });
  }
});
