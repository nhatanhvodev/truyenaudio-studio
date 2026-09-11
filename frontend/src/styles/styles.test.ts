import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

// Bind the base first — see the note in tokens-contrast.test.ts. The literal
// form gets rewritten by Vite into an http:// asset URL and fileURLToPath
// rejects it.
const here = import.meta.url;
const css = readFileSync(fileURLToPath(new URL('./base.css', here)), 'utf8');

const REQUIRED = [
  '--font-ui', '--font-mono', '--font-read',
  '--sp-1', '--sp-2', '--sp-3', '--sp-4', '--sp-5', '--sp-6',
  '--fs-sm', '--fs-md', '--fs-lg', '--fs-read', '--lh-read',
  '--radius', '--focus-ring', '--focus-offset',
  '--density-row', '--density-pad',
];

describe('base.css scale tokens', () => {
  it('declares every scale token exactly once', () => {
    for (const token of REQUIRED) {
      const hits = css.match(new RegExp(`${token}:`, 'g')) ?? [];
      expect(hits.length, `${token} declared ${hits.length} times`).toBe(1);
    }
  });

  it('forces radius to zero', () => {
    expect(css).toMatch(/--radius:\s*0(px|rem)?\s*;/);
  });
});
