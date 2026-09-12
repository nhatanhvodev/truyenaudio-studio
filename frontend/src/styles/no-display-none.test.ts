import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

/**
 * `display: none` is how this app loses a control.
 *
 * The redesign shipped two real regressions that were invisible to every test on
 * the branch: a `@media (max-width: 1023px)` block set `display: none` on the
 * translation navigator and inspector, and another on the project-settings group
 * nav. The QA filter select lives in the translation navigator and is the ONLY
 * control that sets `filter`, so at phone width real state became permanently
 * unreachable — while 26 tests passed.
 *
 * The pre-redesign tree could not have this bug at all: it shipped no CSS files,
 * so every element was laid out by inline styles and nothing could be removed at
 * any width. The reachability specs in `e2e/visual-a11y.spec.ts` now assert the
 * CONSEQUENCE at page level, which is the real proof. This test asserts the
 * MECHANISM, which is cheaper and fails in a second instead of a minute.
 *
 * It is a census, not a ban: `display: none` is legitimate for some things, so a
 * file that genuinely needs it is listed below WITH its reason rather than being
 * quietly tolerated. The app currently uses it nowhere.
 */
const ALLOWED: ReadonlyMap<string, string> = new Map<string, string>([
  // e.g. ['shared/ui/SomeComponent.module.css', 'why this one must be removed from layout'],
]);

// Strip CSS comments, so prose ABOUT display:none is not read as a rule.
function stripCssComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, '');
}

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      walk(full, out);
    } else {
      out.push(full);
    }
  }
  return out;
}

// Bind the base first, then use the VARIABLE: a literal
// `new URL('./base.css', import.meta.url)` gets rewritten by Vite into an
// http:// asset URL, which fileURLToPath rejects. Same workaround, same reason,
// as styles.test.ts and tokens-contrast.test.ts.
const here = import.meta.url;
const anchor = fileURLToPath(new URL('./base.css', here));
const srcRoot = dirname(dirname(anchor));

const allFiles = walk(srcRoot).filter((file) => !/\.test\.tsx?$/.test(file));

describe('no display:none in the stylesheets', () => {
  it('scans a meaningful number of stylesheets', () => {
    // Non-vacuity: a walker that silently returned nothing would make the
    // assertion below pass while proving nothing.
    const cssFiles = allFiles.filter((file) => file.endsWith('.css'));
    expect(cssFiles.length, 'expected to find the app stylesheets').toBeGreaterThan(20);
  });

  it('finds no display:none outside the declared allowlist', () => {
    const offenders: string[] = [];

    for (const file of allFiles) {
      const rel = relative(srcRoot, file).replace(/\\/g, '/');
      if (ALLOWED.has(rel)) continue;

      if (file.endsWith('.css')) {
        const body = stripCssComments(readFileSync(file, 'utf8'));
        if (/display\s*:\s*none/i.test(body)) offenders.push(rel);
      } else if (/\.tsx?$/.test(file)) {
        // The other way to remove an element from layout, and equally invisible
        // to a CSS-only census. The pre-redesign tree used this form throughout.
        const body = readFileSync(file, 'utf8');
        if (/display\s*:\s*['"]none['"]/i.test(body)) offenders.push(rel);
      }
    }

    expect(
      offenders,
      `display:none hides controls from users at some widths. If one of these is ` +
        `genuinely required, add it to ALLOWED with a reason; otherwise remove it.`,
    ).toEqual([]);
  });

  it('documents a reason for every allowlisted file', () => {
    for (const [file, reason] of ALLOWED) {
      expect(reason.trim().length, `${file} needs a real reason`).toBeGreaterThan(10);
    }
  });
});
