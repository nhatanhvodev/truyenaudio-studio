import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

// Bind the base first — see the note in tokens-contrast.test.ts. The literal
// form gets rewritten by Vite into an http:// asset URL and fileURLToPath
// rejects it.
const here = import.meta.url;
const css = readFileSync(fileURLToPath(new URL('./base.css', here)), 'utf8');
const srcRoot = fileURLToPath(new URL('..', here));

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

/**
 * `.visually-hidden` is referenced from plain string className call sites, so it
 * is invisible to the CSS-module type checker and to `tsc` — an undefined class
 * simply renders as ordinary visible text. That is exactly what happened: the
 * class was used by ProfileEditor and WorkspaceTabs while no rule defined it
 * anywhere, so a screen-reader-only label duplicated the visible label beside
 * the password field for the whole life of the file.
 *
 * This test closes that hole from both ends: the rule must exist, and every
 * call site must be covered by it.
 */
describe('.visually-hidden utility', () => {
  it('is defined globally in base.css', () => {
    expect(css).toMatch(/\.visually-hidden:not\(:focus\):not\(:active\)\s*\{/);
  });

  it('hides with clip-path and a 1px box rather than display:none', () => {
    // display:none would remove the content from the accessibility tree as well,
    // which defeats the entire point of the utility.
    const rule = css.slice(css.indexOf('.visually-hidden:not(:focus)'));
    const body = rule.slice(0, rule.indexOf('}'));
    expect(body).toMatch(/clip-path:\s*inset\(50%\)/);
    expect(body).toMatch(/width:\s*1px/);
    expect(body).not.toMatch(/display:\s*none/);
  });

  it('every call site uses the bare global name base.css defines', () => {
    const callSites: string[] = [];
    const walk = (dir: string) => {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const full = join(dir, entry.name);
        if (entry.isDirectory()) {
          walk(full);
        } else if (/\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name)) {
          const text = readFileSync(full, 'utf8');
          if (text.includes('visually-hidden')) callSites.push(full);
        }
      }
    };
    walk(srcRoot);

    // Non-vacuity: if the call sites ever disappear this assertion should be
    // revisited deliberately, not pass on an empty list.
    expect(callSites.length, 'expected at least one .visually-hidden call site').toBeGreaterThan(0);
    for (const file of callSites) {
      const text = readFileSync(file, 'utf8');
      // A `styles.visuallyHidden` form would compile to a HASHED module class and
      // silently stop matching the global rule above.
      expect(text, `${file} must not reference a hashed module form`).not.toMatch(
        /styles\.visuallyHidden\b|styles\[['"]visually-hidden['"]\]/,
      );
    }
  });
});
