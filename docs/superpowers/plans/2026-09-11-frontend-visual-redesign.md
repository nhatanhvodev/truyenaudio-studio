# Frontend Visual Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the entire presentation layer of the Truyện Audio Studio frontend — token system, theme runtime, 12 primitives on CSS Modules, a split router, and the new brutalist/dark-studio visual — without changing a single API call, guard, route, or user-visible string.

**Architecture:** CSS custom properties in `frontend/src/styles/` are the single source of colour truth; `tokens.css` is parsed by the contrast gate so the CSS and the gate can never drift. A boot script in `index.html` resolves the theme before first paint and writes `documentElement.dataset.theme`; `ThemeProvider` seeds from that attribute and owns all later changes. The 12 existing primitives keep their public API and ARIA contract but read `var(--…)` instead of inline `style={{}}`. `router.tsx` splits into `routes/screens/*.tsx` with co-located `.module.css`.

**Tech Stack:** React 19.1.1, Vite 7.1.2, TypeScript 5.8.3, vitest 3.2.4, @playwright/test, CSS Modules (Vite built-in). **Zero new runtime dependencies** — the only addition is `@types/node` as a devDependency, needed by the three gates that read source files from disk.

**Spec:** [docs/superpowers/specs/2026-09-11-frontend-visual-redesign-design.md](../specs/2026-09-11-frontend-visual-redesign-design.md)
**ADR:** [docs/architecture/adr/0002-dark-palette-and-border-tokens.md](../../architecture/adr/0002-dark-palette-and-border-tokens.md)

## Global Constraints

Copied verbatim from the spec. Every task's requirements implicitly include this section.

- **Copy người dùng đóng băng.** `App.test.tsx` asserts on visible copy and accessible names. Do not reword, translate, re-case, or re-punctuate any of these. Full list in spec §6.
- **Không chạm** API call, state, routing behavior, guard, backend.
- **Không thêm dependency runtime.** Không thư viện UI, CSS-in-JS, design system, hay bất cứ thứ gì vào bundle. Ngoại lệ duy nhất được chốt: `@types/node` là **devDependency type-only** — cần cho ba gate đọc file nguồn từ đĩa (`node:fs`/`node:url`), không vào bundle, không có mặt lúc chạy. Không thêm gì khác mà không hỏi trước.
- **Không download font.** System font stack only.
- **IA khóa:** navigator 240px · inspector 320px · split clamp 25–75% · tối đa 8 tab tài liệu (ADR-0001 D4) · dưới 1024px inspector thành drawer, source/translation thành tab.
- **Không có màu nào là tín hiệu duy nhất** — trạng thái phải kèm chữ hoặc hình.
- **Radius = 0.** No border-radius anywhere except where a native control forces it.
- **`text-transform: uppercase` chỉ cho nhãn giao diện.** Never on source or target prose (destroys Vietnamese diacritics and is meaningless on CJK).
- **No raw hex in `.module.css`.** Every colour comes from a `var(--…)`.
- **Gate commands:** `cd frontend && npx vitest run` · `npm run build` · `npx playwright test`.

## Conventions

These are used by every task from G2 onward. Defined once here; tasks reference them.

**Colour tokens** (defined in Task 1, never redefined):

| Token | Dark (default) | Light |
|---|---|---|
| `--bg` | `#0B0C0E` | `#F7F8FA` |
| `--surface` | `#101216` | `#FFFFFF` |
| `--surface-raised` | `#16191E` | `#F1F3F7` |
| `--border-subtle` | `#262A31` | `#D1D5DB` |
| `--border-control` | `#616A78` | `#6B7280` |
| `--text` | `#E8EAED` | `#172033` |
| `--text-muted` | `#9AA1AB` | `#4B5563` |
| `--text-subtle` | `#7A828D` | `#5C6675` |
| `--primary` | `#FF6B2C` | `#1D4ED8` |
| `--primary-hover` | `#FF8551` | `#1E40AF` |
| `--on-primary` | `#0B0C0E` | `#FFFFFF` |
| `--danger` | `#FF5A3C` | `#B91C1C` |
| `--danger-hover` | `#FF7A61` | `#991B1B` |
| `--success` | `#4ADE80` | `#166534` |
| `--warning` | `#FBBF24` | `#92400E` |
| `--focus` | `#7DD3FC` | `#1D4ED8` |

**Scale tokens:** `--sp-1:0.25rem --sp-2:0.5rem --sp-3:0.75rem --sp-4:1rem --sp-5:1.5rem --sp-6:2rem`
`--fs-sm:0.8125rem --fs-md:0.875rem --fs-lg:1rem --fs-read:1.125rem --lh-read:1.7`
`--font-ui` (system stack) · `--font-mono` (Consolas stack) · `--font-read` (adds CJK fallbacks)
`--radius:0` · `--focus-ring:2px` · `--focus-offset:2px` · `--density-row:2.25rem` · `--density-pad:0.75rem`

**Focus ring pattern** — every focusable element, no exceptions:

```css
.thing:focus-visible {
  outline: var(--focus-ring) solid var(--focus);
  outline-offset: var(--focus-offset);
}
```

**Primitive module pattern** (Task 5 onwards):

```css
/* Button.module.css */
.button {
  font-family: var(--font-ui);
  font-size: var(--fs-md);
  border-radius: var(--radius);
  border: 1px solid transparent;
  padding: var(--sp-2) var(--sp-4);
  cursor: pointer;
}
.button:disabled { cursor: not-allowed; opacity: 0.55; }
.primary { background: var(--primary); color: var(--on-primary); }
.primary:hover:not(:disabled) { background: var(--primary-hover); }
.secondary { background: var(--surface-raised); color: var(--text); border-color: var(--border-control); }
.ghost { background: transparent; color: var(--text); border-color: var(--border-control); }
.danger { background: var(--danger); color: var(--on-primary); }
.danger:hover:not(:disabled) { background: var(--danger-hover); }
```

**Screen module pattern:** grid/flex layout, `min-width: 0` on every grid child that can hold long prose (the U05/`171d2d6` overflow bug), `1px solid var(--border-subtle)` for separators, `1px solid var(--border-control)` for control outlines.

---

## File Structure

**Create:**
```
frontend/src/styles/tokens.css          colour + scale custom properties, :root dark, [data-theme='light']
frontend/src/styles/reset.css           minimal reset
frontend/src/styles/base.css            html/body, font stacks, root font-size for fontScale
frontend/src/features/settings/themeRuntime.ts   apply/resolve/subscribe preferences
frontend/src/shared/ui/*.module.css     12 files, one per primitive
frontend/src/routes/Shell.tsx           app shell + nav + workspace tabs
frontend/src/routes/screens/*.tsx       10 screen files + co-located .module.css
```

**Modify:**
```
frontend/index.html                     boot script (no theme flash)
frontend/src/main.tsx                   import styles/
frontend/src/App.tsx                    mount ThemeProvider
frontend/src/shared/ui/*.tsx            12 primitives to CSS Modules
frontend/src/shared/ui/index.ts         stop exporting tokens
frontend/src/shared/ui/tokens-contrast.test.ts   parse tokens.css, 5 → 17 pairs
frontend/src/features/settings/uiPreferences.ts  default theme system → dark (one line)
frontend/src/features/settings/AppearanceSettings.tsx  notify themeRuntime on save
frontend/src/routes/router.tsx          becomes the route table only
frontend/e2e/visual-a11y.spec.ts        extend for the new surfaces
```

**Delete:**
```
frontend/src/shared/ui/tokens.ts        replaced by tokens.css (no screen imports it)
```

---

# G1 — Token và theme

## Task 1: `tokens.css` as the single colour source, with a gate that parses it

**Files:**
- Create: `frontend/src/styles/tokens.css`
- Modify: `frontend/src/shared/ui/tokens-contrast.test.ts`
- Test: `frontend/src/shared/ui/tokens-contrast.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces: `tokens.css` with `:root` (dark) and `[data-theme='light']` blocks. Token names in the Conventions table. Every later task reads these names.

- [ ] **Step 1: Write the failing test**

Replace `frontend/src/shared/ui/tokens-contrast.test.ts` entirely. It now parses the CSS instead of importing TypeScript, so the stylesheet and the gate cannot drift apart.

```ts
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

// Bind the base first. Vite rewrites `new URL('<literal>', import.meta.url)`
// into a dev-server asset URL, and fileURLToPath then throws "The URL must be
// of scheme file". Binding import.meta.url to a variable first keeps the
// protocol at file: so the path resolves against the real filesystem.
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

const css = readFileSync(CSS_PATH, 'utf8');

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/shared/ui/tokens-contrast.test.ts`
Expected: FAIL — `Error: selector :root not found in tokens.css` (the file does not exist yet, so `readFileSync` throws ENOENT first).

- [ ] **Step 3: Create `frontend/src/styles/tokens.css`**

```css
/*
 * Single source of colour truth (ADR-0002).
 * :root carries the DARK set — that is the default, and it stays correct even
 * if the boot script never runs. [data-theme='light'] carries the light set.
 * The contrast gate parses this file, so changing a value here is gated.
 */

:root {
  color-scheme: dark;

  --bg: #0B0C0E;
  --surface: #101216;
  --surface-raised: #16191E;
  --border-subtle: #262A31;
  --border-control: #616A78;

  --text: #E8EAED;
  --text-muted: #9AA1AB;
  --text-subtle: #7A828D;

  --primary: #FF6B2C;
  --primary-hover: #FF8551;
  --on-primary: #0B0C0E;

  --danger: #FF5A3C;
  --danger-hover: #FF7A61;
  --success: #4ADE80;
  --warning: #FBBF24;
  --focus: #7DD3FC;
}

[data-theme='light'] {
  color-scheme: light;

  --bg: #F7F8FA;
  --surface: #FFFFFF;
  --surface-raised: #F1F3F7;
  --border-subtle: #D1D5DB;
  --border-control: #6B7280;

  --text: #172033;
  --text-muted: #4B5563;
  --text-subtle: #5C6675;

  --primary: #1D4ED8;
  --primary-hover: #1E40AF;
  --on-primary: #FFFFFF;

  --danger: #B91C1C;
  --danger-hover: #991B1B;
  --success: #166534;
  --warning: #92400E;
  --focus: #1D4ED8;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/shared/ui/tokens-contrast.test.ts`
Expected: PASS — 34 tests (17 pairs × 2 themes).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/styles/tokens.css frontend/src/shared/ui/tokens-contrast.test.ts
git commit -m "feat(ui): make tokens.css the single colour source and gate it by parsing the file"
```

---

## Task 2: Reset, base styles, and wire the stylesheet into the bundle

**Files:**
- Create: `frontend/src/styles/reset.css`, `frontend/src/styles/base.css`
- Modify: `frontend/src/main.tsx`
- Test: `frontend/src/styles/styles.test.ts` (create)

**Interfaces:**
- Consumes: `tokens.css` (Task 1).
- Produces: `--font-ui`, `--font-mono`, `--font-read`, `--sp-1..6`, `--fs-sm|md|lg|read`, `--lh-read`, `--radius`, `--focus-ring`, `--focus-offset`, `--density-row`, `--density-pad` — every scale token G2–G6 reads.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/styles/styles.test.ts`. Asserting the scale tokens exist catches the failure mode where a primitive silently falls back to a browser default:

```ts
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/styles/styles.test.ts`
Expected: FAIL — ENOENT, `base.css` does not exist.

- [ ] **Step 3: Create the two stylesheets**

`frontend/src/styles/reset.css`:

```css
*, *::before, *::after { box-sizing: border-box; }
* { margin: 0; }
html, body, #root { height: 100%; }
img, svg, video, canvas { display: block; max-width: 100%; }
input, button, textarea, select { font: inherit; color: inherit; }
button { background: none; border: none; padding: 0; }
h1, h2, h3, h4 { font-size: inherit; font-weight: inherit; }
ul, ol { list-style: none; padding: 0; }
table { border-collapse: collapse; }
```

`frontend/src/styles/base.css`:

```css
/*
 * Scale tokens. fontScale is implemented by changing the ROOT font-size, so
 * every rem in the app scales together and browser zoom still works on top.
 * The values here are the "medium" defaults; Task 9 overrides them per
 * preference by setting documentElement.style.fontSize.
 */

:root {
  --font-ui: system-ui, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
  --font-mono: Consolas, ui-monospace, SFMono-Regular, 'Courier New', monospace;
  --font-read: var(--font-ui), 'Noto Sans CJK SC', 'Microsoft YaHei', sans-serif;

  --sp-1: 0.25rem;
  --sp-2: 0.5rem;
  --sp-3: 0.75rem;
  --sp-4: 1rem;
  --sp-5: 1.5rem;
  --sp-6: 2rem;

  --fs-sm: 0.8125rem;
  --fs-md: 0.875rem;
  --fs-lg: 1rem;
  --fs-read: 1.125rem;
  --lh-read: 1.7;

  --radius: 0;
  --focus-ring: 2px;
  --focus-offset: 2px;

  --density-row: 2.25rem;
  --density-pad: 0.75rem;
}

html { font-size: 100%; }

body {
  background: var(--bg);
  color: var(--text);
  font-family: var(--font-ui);
  font-size: var(--fs-md);
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}

/* Diagnostics, costs and identifiers only — never prose. */
.mono { font-family: var(--font-mono); font-size: var(--fs-sm); }

/* Reading surfaces: source and translated prose. Never uppercased. */
.reading {
  font-family: var(--font-read);
  font-size: var(--fs-read);
  line-height: var(--lh-read);
  text-transform: none;
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}
```

- [ ] **Step 4: Import both stylesheets in `frontend/src/main.tsx`**

Order matters: reset, then tokens, then base. Tokens must come before base so base can reference them.

```tsx
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';

import './styles/reset.css';
import './styles/tokens.css';
import './styles/base.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
```

- [ ] **Step 5: Run tests and build**

Run: `cd frontend && npx vitest run src/styles && npm run build`
Expected: styles tests PASS. Build PASS and — this is the point of the task — `dist/assets/` now contains a **`.css` asset**, where before the build produced none.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/styles/reset.css frontend/src/styles/base.css frontend/src/styles/styles.test.ts frontend/src/main.tsx
git commit -m "feat(ui): add reset and base stylesheets with scale tokens, wire into the bundle"
```

---

## Task 3: Theme runtime and the no-flash boot script

> **Run order:** Task 4 runs **before** this task. This task's boot script defaults the theme to
> `dark`, and until Task 4 lands `defaultPreferences().theme` is still `'system'` — so the boot
> script and the app would disagree and the parity test below would be asserting agreement that
> does not exist. Task 4 is a one-line change to an existing file and depends on nothing here.


**Files:**
- Create: `frontend/src/features/settings/themeRuntime.ts`, `frontend/src/features/settings/themeRuntime.test.ts`
- Create: `frontend/src/features/settings/ThemeProvider.tsx`
- Modify: `frontend/index.html`, `frontend/src/App.tsx`
- Test: `frontend/src/features/settings/themeRuntime.test.ts`

**Interfaces:**
- Consumes: `parsePreferences`, `defaultPreferences`, `UiPreferences`, `Theme` from `./uiPreferences`; `STORAGE_KEY` concept.
- Produces:
  - `UI_PREFERENCES_STORAGE_KEY: string` = `'studio.ui-preferences'`
  - `resolveTheme(theme: Theme, systemPrefersDark: boolean): 'light' | 'dark'`
  - `applyPreferences(prefs: UiPreferences): void`
  - `readStoredPreferences(): UiPreferences`
  - `subscribePreferences(listener: (prefs: UiPreferences) => void): () => void`
  - `notifyPreferencesChanged(): void`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/features/settings/themeRuntime.test.ts`:

```ts
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/features/settings/themeRuntime.test.ts`
Expected: FAIL — cannot resolve `./themeRuntime`.

- [ ] **Step 3: Create `frontend/src/features/settings/themeRuntime.ts`**

The boot script in `index.html` cannot import this module (it runs before the bundle), so `resolveTheme` is duplicated there in three lines. Step 6 adds a test that pins both to the same behaviour.

```ts
import { parsePreferences, type Theme, type UiPreferences } from './uiPreferences';

export const UI_PREFERENCES_STORAGE_KEY = 'studio.ui-preferences';

/** Root font sizes that implement the fontScale preference. */
const ROOT_FONT_SIZE: Record<UiPreferences['fontScale'], string> = {
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
```

- [ ] **Step 4: Create `frontend/src/features/settings/ThemeProvider.tsx`**

Seeds from the attribute the boot script already wrote, so the theme is never re-resolved and there is no second source of truth:

```tsx
import { useEffect, type ReactNode } from 'react';

import { applyPreferences, readStoredPreferences, subscribePreferences } from './themeRuntime';

/**
 * Owns every LATER change. The boot script in index.html already resolved
 * data-theme before this bundle ran, so the provider never resolves a second
 * time — it re-applies the stored document and then reacts to changes.
 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  useEffect(() => {
    // Idempotent at startup: the boot script wrote these from the raw
    // localStorage value. parsePreferences sanitizes that value, so this is
    // also what repairs a malformed or tampered stored document.
    applyPreferences(readStoredPreferences());
    return subscribePreferences((preferences) => {
      applyPreferences(preferences);
    });
  }, []);

  // System colour-scheme changes while the preference is "system".
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return;
    const query = window.matchMedia('(prefers-color-scheme: dark)');
    const onChange = () => applyPreferences(readStoredPreferences());
    query.addEventListener('change', onChange);
    return () => query.removeEventListener('change', onChange);
  }, []);

  return <>{children}</>;
}
```

No `ready` gate and no theme state: the boot script has already written theme, density and root font size before React mounts, so children render correct on their first paint. Withholding the first render would blank the app for a frame and buy nothing.

- [ ] **Step 5: Add the boot script to `frontend/index.html`**

`index.html` today is exactly two lines — `<div id="root"></div>` and the module script. **Replace
its whole contents with the block below.** Do not append: the snippet repeats `<div id="root">`,
and a second root element would make `document.getElementById('root')` ambiguous.


```html
<div id="root"></div>
<script>
  (function () {
    var SIZES = { small: '87.5%', medium: '100%', large: '112.5%' };
    var root = document.documentElement;
    var prefs = {};
    try {
      var raw = window.localStorage.getItem('studio.ui-preferences');
      if (raw) prefs = JSON.parse(raw) || {};
    } catch (error) {
      prefs = {};
    }

    var theme = prefs.theme;
    // No usable stored choice means the default preference, which is dark.
    // Only an explicit "system" follows the OS — otherwise a new user on a
    // light-mode OS would flash light and then snap to dark.
    if (theme !== 'light' && theme !== 'dark' && theme !== 'system') {
      theme = 'dark';
    }
    if (theme === 'system') {
      theme =
        window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches
          ? 'dark'
          : 'light';
    }
    root.dataset.theme = theme;

    root.dataset.density = prefs.density === 'compact' ? 'compact' : 'comfortable';
    root.style.fontSize = SIZES[prefs.fontScale] || '100%';
  })();
</script>
<script type="module" src="/src/main.tsx"></script>
```

- [ ] **Step 6: Add a boot-script parity test**

The boot script duplicates `resolveTheme`'s logic. Pin them together by extracting the script body from `index.html` and evaluating it against each case. Append to `themeRuntime.test.ts`:

```ts
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

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
  // dark is what defaultPreferences() returns — Task 4 made that true, and it
  // runs before this task. Only an explicit 'system' follows the OS. This is the
  // exact drift this test exists to catch: the boot script carries its own copy
  // of the normalise-then-resolve logic, and the day the two copies disagree,
  // the app paints one theme and snaps to the other the moment React mounts.
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
```

- [ ] **Step 7: Mount `ThemeProvider` in `frontend/src/App.tsx`**

```tsx
import { RouterProvider } from 'react-router-dom';
import { useEffect } from 'react';

import { ThemeProvider } from './features/settings/ThemeProvider';
import { router } from './routes/router';

export default function App() {
  useEffect(() => {
    // Discard the legacy persisted credential without reading or reusing it.
    window.localStorage.removeItem('gemini_api_key');
  }, []);

  return (
    <ThemeProvider>
      <RouterProvider router={router} />
    </ThemeProvider>
  );
}
```

- [ ] **Step 8: Run tests**

Run: `cd frontend && npx vitest run src/features/settings src/shared/ui && npm run build`
Expected: themeRuntime tests PASS, including all **8** parity cases. `App.test.tsx` and the rest still PASS.

`ThemeProvider` renders its children on the **first** paint — it has no `ready` gate and holds no state, because the boot script has already written theme, density and root font size before the bundle runs. So wrapping `App` in it must not delay any existing assertion, and no test should need a retry or a longer timeout. If a test fails here, it is a real finding: report it. Do not add a retry, a `waitFor`, or a `ready` gate to make it pass.

- [ ] **Step 9: Commit**

```bash
git add frontend/index.html frontend/src/App.tsx frontend/src/features/settings/themeRuntime.ts frontend/src/features/settings/themeRuntime.test.ts frontend/src/features/settings/ThemeProvider.tsx
git commit -m "feat(ui): add theme runtime and a no-flash boot script, mounted at the app root"
```

---

## Task 4: Make dark the actual default preference

> **Run order:** this task runs **before** Task 3. It is a one-line change to an existing file and
> depends on nothing in Task 3. Task 3's boot script already defaults to `dark`, so until this
> lands the boot script and `defaultPreferences()` disagree — a fresh profile on a light-mode OS
> paints dark, then snaps to light when React mounts. Doing this first removes that window, and it
> is what lets Task 3's parity test assert real agreement. This task's test therefore goes in
> `uiPreferences.test.ts` (which exists today), not in `themeRuntime.test.ts` (Task 3's artifact).

**Files:**
- Modify: `frontend/src/features/settings/uiPreferences.ts:51`
- Modify: `frontend/src/features/settings/AppearanceSettings.test.tsx`
- Test: `frontend/src/features/settings/uiPreferences.test.ts` (append)

**Interfaces:**
- Consumes: `defaultPreferences` (existing).
- Produces: `defaultPreferences().theme === 'dark'`. Task 3's parity test depends on this exact value.

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/features/settings/uiPreferences.test.ts`:

```ts
describe('dark is the default preference', () => {
  it('defaults to dark, not system', () => {
    expect(defaultPreferences().theme).toBe('dark');
  });

  it('keeps system available as an explicit choice', () => {
    expect(THEMES).toContain('system');
    expect(THEMES).toHaveLength(3);
  });

  it('falls back to dark when the stored theme is not a known value', () => {
    expect(parsePreferences(JSON.stringify({ version: 1, theme: 'neon' })).preferences.theme).toBe(
      'dark',
    );
  });
});
```

Add `THEMES` and `parsePreferences` to that file's existing import from `./uiPreferences` if they
are not already imported. Do not add a second import statement for the same module.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/features/settings/uiPreferences.test.ts -t "dark is the default"`
Expected: FAIL — `defaultPreferences().theme` is `'system'`, received `'system'` want `'dark'`.

- [ ] **Step 3: Change the one line in `uiPreferences.ts`**

At line 51, inside `defaultPreferences()`, change `theme: 'system'` to:

```ts
    theme: 'dark',
```

Leave the doc comment above `defaultPreferences()` accurate. `THEMES` still lists all three values —
`'system'` stays a valid *explicit* choice, it is simply no longer the default.

- [ ] **Step 4: Run the full frontend suite**

Run: `cd frontend && npx vitest run`

Expected: PASS **after** fixing two now-stale expectations in
`frontend/src/features/settings/AppearanceSettings.test.tsx`. An earlier draft of this task claimed
that file was unaffected; it is not — both of these assert the old default, not behaviour:

- around line 51 — the corrupt-document save test asserts the rewritten document equals
  `{ version: 1, theme: 'system', … }`. The component seeds its state from `defaultPreferences()`,
  so the saved theme is now `'dark'`. Change that one value; leave every other key.
- around line 68 — the reset-to-defaults test asserts `getByLabelText('Theme')` has value
  `'system'`. Change it to `'dark'`.

Both keep their meaning with `'dark'`: the corrupt-doc test's point is that the rewritten document
is *valid and minimal*, and the reset test's point is that the control shows *the default*. If any
**other** test fails, stop and report it — do not edit the default to make it pass, and do not
weaken a genuine assertion.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/settings/uiPreferences.ts frontend/src/features/settings/uiPreferences.test.ts frontend/src/features/settings/AppearanceSettings.test.tsx
git commit -m "feat(ui): default the theme preference to dark, keeping system as an explicit option"
```

---

# G2 — Primitives on CSS Modules

Every task in G2 keeps the primitive's public props and ARIA exactly as they are, except for removing the `theme` prop. Removing it is safe: grep confirms no caller outside `shared/ui/` ever passes it.

## Task 5: Button, Input, Select

> **Owned by no task — already landed.** There is no G1 task that makes `*.module.css` typecheck,
> and every G2 task imports one. `tsconfig.json` has `include: ["src"]` and no `types` field, and
> the project had no `vite-env.d.ts`, so the first CSS-module import would have turned `tsc -b`
> (the first half of `npm run build`) red with `TS2307: Cannot find module './X.module.css' or its
> corresponding type declarations`. `frontend/src/vite-env.d.ts`, containing only
> `/// <reference types="vite/client" />`, was added by the controller before this task and verified
> causally (probe import fails without it, `tsc -b --force` exits 0 with it). It adds no dependency —
> `vite` is already a devDependency and ships the client types — and nothing enters the bundle.
> Do not re-add it and do not delete it.

**Files:**
- Create: `frontend/src/shared/ui/Button.module.css`, `Input.module.css`, `Select.module.css`
- Modify: `frontend/src/shared/ui/Button.tsx`, `Input.tsx`, `Select.tsx`
- Test: `frontend/src/shared/ui/ui.test.tsx` (existing, must stay green)

**Interfaces:**
- Consumes: scale and colour tokens (Tasks 1–2).
- Produces: `Button`, `Input`, `Select` with unchanged props minus `theme`.

- [ ] **Step 1: Read the existing test contract**

Run: `cd frontend && npx vitest run src/shared/ui/ui.test.tsx`
Expected: PASS. Note the exact assertions — `aria-busy="true"` while loading, `type="button"`, wired label/error/description ids, `aria-invalid`, `role="alert"` for errors, `*` for required, `role="option"` for Select options. These are the contract for this task.

- [ ] **Step 2: Create `Button.module.css`**

Use the pattern in Conventions, plus the focus ring:

```css
.button {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-2);
  font-family: var(--font-ui);
  font-size: var(--fs-md);
  font-weight: 500;
  line-height: 1.25;
  border-radius: var(--radius);
  border: 1px solid transparent;
  padding: var(--sp-2) var(--sp-4);
  cursor: pointer;
}

.button:focus-visible {
  outline: var(--focus-ring) solid var(--focus);
  outline-offset: var(--focus-offset);
}

.button:disabled { cursor: not-allowed; opacity: 0.55; }

.primary { background: var(--primary); color: var(--on-primary); }
.primary:hover:not(:disabled) { background: var(--primary-hover); }

.secondary { background: var(--surface-raised); color: var(--text); border-color: var(--border-control); }
.secondary:hover:not(:disabled) { background: var(--surface); }

.ghost { background: transparent; color: var(--text); border-color: var(--border-control); }
.ghost:hover:not(:disabled) { border-color: var(--text); }

.danger { background: var(--danger); color: var(--on-primary); }
.danger:hover:not(:disabled) { background: var(--danger-hover); }
```

- [ ] **Step 3: Rewrite `Button.tsx`**

Delete `backgroundFor`, `hoverFor`, the inline `style` object, and every mouse/focus handler that mutated `style` imperatively (CSS `:hover`/`:focus-visible` replaces them). Keep `type="button"`, `aria-busy`, `disabled`.

```tsx
import type { ButtonHTMLAttributes } from 'react';

import styles from './Button.module.css';

type Variant = 'primary' | 'secondary' | 'danger' | 'ghost';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  loading?: boolean;
}

export function Button({
  variant = 'primary',
  loading = false,
  disabled,
  children,
  className,
  ...rest
}: ButtonProps) {
  const isDisabled = Boolean(disabled || loading);
  return (
    <button
      type="button"
      {...rest}
      className={[styles.button, styles[variant], className].filter(Boolean).join(' ')}
      disabled={isDisabled}
      aria-busy={loading || undefined}
    >
      {children}
    </button>
  );
}
```

- [ ] **Step 4: Run the Button tests**

Run: `cd frontend && npx vitest run src/shared/ui/ui.test.tsx`
Expected: PASS for the Button and `aria-busy` cases. Input/Select cases still pass unchanged at this point.

- [ ] **Step 5: Create `Input.module.css` and `Select.module.css`**

```css
/* Input.module.css */
.field {
  display: block;
  font-family: var(--font-ui);
  font-size: var(--fs-md);
  background: var(--surface);
  color: var(--text);
  border: 1px solid var(--border-control);
  border-radius: var(--radius);
  padding: var(--sp-2) var(--sp-3);
  width: 100%;
  min-width: 0;
}
.field:focus-visible {
  outline: var(--focus-ring) solid var(--focus);
  outline-offset: var(--focus-offset);
}
.invalid { border-color: var(--danger); }
.label { display: block; font-size: var(--fs-sm); color: var(--text-muted); margin-bottom: var(--sp-1); }
.required { color: var(--danger); margin-left: var(--sp-1); }
.error { color: var(--danger); font-size: var(--fs-sm); margin-top: var(--sp-1); }
.description { color: var(--text-subtle); font-size: var(--fs-sm); margin-top: var(--sp-1); }
```

```css
/* Select.module.css */
.select {
  font-family: var(--font-ui);
  font-size: var(--fs-md);
  background: var(--surface);
  color: var(--text);
  border: 1px solid var(--border-control);
  border-radius: var(--radius);
  padding: var(--sp-2) var(--sp-3);
  width: 100%;
  min-width: 0;
}
.select:focus-visible {
  outline: var(--focus-ring) solid var(--focus);
  outline-offset: var(--focus-offset);
}
.option { font-family: var(--font-ui); }
```

- [ ] **Step 6: Rewrite `Input.tsx` and `Select.tsx` against these modules**

Replace the inline `style` objects with `className` from the modules. Keep every id-wiring line and every ARIA attribute byte-for-byte — `ui.test.tsx` asserts them. Keep `role="alert"` on the error node and `role="option"` on Select options.

- [ ] **Step 7: Run tests and build**

Run: `cd frontend && npx vitest run src/shared/ui && npm run build`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/shared/ui/Button.tsx frontend/src/shared/ui/Button.module.css frontend/src/shared/ui/Input.tsx frontend/src/shared/ui/Input.module.css frontend/src/shared/ui/Select.tsx frontend/src/shared/ui/Select.module.css
git commit -m "refactor(ui): move Button, Input and Select onto CSS Modules"
```

---

## Task 6: Combobox and Tabs

**Files:**
- Create: `frontend/src/shared/ui/Combobox.module.css`, `Tabs.module.css`
- Modify: `frontend/src/shared/ui/Combobox.tsx`, `Tabs.tsx`
- Test: `frontend/src/shared/ui/combobox-drawer.test.tsx`, `modal-tabs.test.tsx`

**Interfaces:**
- Consumes: tokens (Tasks 1–2).
- Produces: `Combobox`, `Tabs` with unchanged props minus `theme`.

- [ ] **Step 1: Run the existing tests for the contract**

Run: `cd frontend && npx vitest run src/shared/ui/combobox-drawer.test.tsx src/shared/ui/modal-tabs.test.tsx`
Expected: PASS. Note the Combobox keyboard/active-option assertions and the Tabs `role="tablist"`/`role="tab"`/`aria-selected` and arrow-key assertions.

- [ ] **Step 2: Create `Combobox.module.css`**

```css
.wrapper { position: relative; min-width: 0; }
.input {
  font-family: var(--font-ui);
  font-size: var(--fs-md);
  background: var(--surface);
  color: var(--text);
  border: 1px solid var(--border-control);
  border-radius: var(--radius);
  padding: var(--sp-2) var(--sp-3);
  width: 100%;
  min-width: 0;
}
.input:focus-visible {
  outline: var(--focus-ring) solid var(--focus);
  outline-offset: var(--focus-offset);
}
.list {
  position: absolute;
  z-index: 20;
  top: 100%;
  left: 0;
  right: 0;
  background: var(--surface-raised);
  border: 1px solid var(--border-control);
  border-radius: var(--radius);
  max-height: 16rem;
  overflow-y: auto;
}
.option {
  font-size: var(--fs-md);
  color: var(--text);
  padding: var(--sp-2) var(--sp-3);
  cursor: pointer;
}
.option[aria-selected='true'] {
  background: var(--primary);
  color: var(--on-primary);
}
.empty { color: var(--text-subtle); font-size: var(--fs-sm); padding: var(--sp-2) var(--sp-3); }
```

- [ ] **Step 3: Create `Tabs.module.css`**

```css
.tablist {
  display: flex;
  gap: 0;
  border-bottom: 1px solid var(--border-subtle);
  overflow-x: auto;
}
.tab {
  font-family: var(--font-ui);
  font-size: var(--fs-sm);
  font-weight: 500;
  letter-spacing: 0.04em;
  text-transform: uppercase; /* interface label only — never prose */
  color: var(--text-muted);
  background: transparent;
  border: none;
  border-bottom: 2px solid transparent;
  padding: var(--sp-2) var(--sp-4);
  cursor: pointer;
  white-space: nowrap;
}
.tab:hover { color: var(--text); }
.tab[aria-selected='true'] {
  color: var(--text);
  border-bottom-color: var(--primary);
}
.tab:focus-visible {
  outline: var(--focus-ring) solid var(--focus);
  outline-offset: calc(-1 * var(--focus-offset));
}
.panel { padding-top: var(--sp-4); }
```

- [ ] **Step 4: Rewrite `Combobox.tsx` and `Tabs.tsx`**

Swap inline styles for module classes. Keep the active-option index logic, `role="option"`, `aria-selected`, `aria-activedescendant`, and every keyboard handler untouched.

- [ ] **Step 5: Run tests**

Run: `cd frontend && npx vitest run src/shared/ui && npm run build`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/shared/ui/Combobox.tsx frontend/src/shared/ui/Combobox.module.css frontend/src/shared/ui/Tabs.tsx frontend/src/shared/ui/Tabs.module.css
git commit -m "refactor(ui): move Combobox and Tabs onto CSS Modules"
```

---

## Task 7: Modal, Drawer, Tooltip, Toast

**Files:**
- Create: `frontend/src/shared/ui/Modal.module.css`, `Drawer.module.css`, `Tooltip.module.css`, `Toast.module.css`
- Modify: `frontend/src/shared/ui/Modal.tsx`, `Drawer.tsx`, `Tooltip.tsx`, `Toast.tsx`
- Test: `frontend/src/shared/ui/modal-tabs.test.tsx`, `feedback-table.test.tsx`, `combobox-drawer.test.tsx`

**Interfaces:**
- Consumes: tokens (Tasks 1–2).
- Produces: `Modal`, `Drawer`, `Tooltip`, `Toast` with unchanged props minus `theme`; `Toast` no longer imports `ColorTokens`.

- [ ] **Step 1: Run the existing tests**

Run: `cd frontend && npx vitest run src/shared/ui/modal-tabs.test.tsx src/shared/ui/feedback-table.test.tsx src/shared/ui/combobox-drawer.test.tsx`
Expected: PASS. Note the Modal focus-trap and focus-return assertions, and the Drawer `role="dialog"` assertions.

**Correction to earlier plan text: the Toast is `role="status"` with `aria-live="polite"`, not
`role="alert"`.** `Toast.tsx:20` reads `role="status"`, `aria-live="polite"` — polite, because a toast
must not interrupt what the screen reader is already saying. Some earlier plan text called it
`role="alert"`; that was wrong. **Keep `role="status"` and `aria-live="polite"` exactly as they are.**
Do not "restore" `role="alert"` — that would be a real ARIA change, and it is the opposite of the
intent.

- [ ] **Step 2: Create the four modules**

```css
/* Modal.module.css */
.backdrop {
  position: fixed;
  inset: 0;
  background: rgb(0 0 0 / 0.6);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}
.dialog {
  background: var(--surface-raised);
  color: var(--text);
  border: 1px solid var(--border-control);
  border-radius: var(--radius);
  padding: var(--sp-5);
  max-width: min(40rem, calc(100vw - 2 * var(--sp-4)));
  max-height: calc(100vh - 2 * var(--sp-4));
  overflow-y: auto;
  min-width: 0;
}
.title { font-size: var(--fs-lg); font-weight: 600; margin-bottom: var(--sp-3); }
```

```css
/* Drawer.module.css */
.panel {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  width: min(20rem, 100vw);
  background: var(--surface-raised);
  color: var(--text);
  border-left: 1px solid var(--border-control);
  border-radius: var(--radius);
  padding: var(--sp-4);
  overflow-y: auto;
  z-index: 100;
  min-width: 0;
}
.title { font-size: var(--fs-lg); font-weight: 600; margin-bottom: var(--sp-3); }
```

```css
/* Tooltip.module.css */
.tip {
  background: var(--surface-raised);
  color: var(--text);
  border: 1px solid var(--border-control);
  border-radius: var(--radius);
  padding: var(--sp-1) var(--sp-2);
  font-size: var(--fs-sm);
  max-width: 20rem;
  z-index: 120;
}
```

```css
/* Toast.module.css */
.toast {
  display: flex;
  align-items: flex-start;
  gap: var(--sp-2);
  background: var(--surface-raised);
  color: var(--text);
  border: 1px solid var(--border-control);
  border-left: 3px solid var(--text-muted);
  border-radius: var(--radius);
  padding: var(--sp-3) var(--sp-4);
  font-size: var(--fs-md);
  min-width: 0;
}
.toneSuccess { border-left-color: var(--success); color: var(--success); }
.toneDanger { border-left-color: var(--danger); color: var(--danger); }
```

Two deliberate changes to the listing above, both to preserve behaviour the current component has:

- **The tone classes set `color` as well as `border-left-color`.** `Toast.tsx:9-13` maps each tone to a
  distinct text colour through `TONE_TEXT` (`success` → `successText`, `danger` → `danger`), and the
  component renders `color: textColor`. Keeping only the border colour would silently drop that
  distinction. Two classes rather than three, because `ToastProps['tone']` is `'info' | 'success' |
  'danger'` — there is no `warning` tone, so a `.warning` rule would be dead CSS. `info` keeps the base
  `.toast` colour.

- **The `boxShadow` the current component sets inline is intentionally dropped**, along with
  `borderRadius: 6`. Radius is 0 across this redesign and the brutalist direction has no elevation
  shadows. This is the one visual property the task removes on purpose; do not add a shadow back.

Note: the tone is currently conveyed by colour alone (the border, plus the text colour above), which the
Global Constraints say a variant must not rely on. That is **pre-existing behaviour, not something this
task introduces** — do not invent a new user-visible label to fix it, because copy is frozen. Preserve
the current behaviour exactly and flag it in your report; it goes to the final review as its own finding.

- [ ] **Step 3: Rewrite the four components**

Swap inline styles for module classes. Keep the Modal's focus trap, Escape handling and focus return; the Drawer's `role="dialog"` and label wiring; the Tooltip's `aria-describedby` wiring; the Toast's `role="status"` and `aria-live="polite"`.

Remove the `ColorTokens` import from `Toast.tsx`. That import is used for the `TONE_TEXT` record's type
(`Record<NonNullable<ToastProps['tone']>, keyof ColorTokens>` at `Toast.tsx:9`), which nothing else in the
file needs once the colours come from CSS. **Replace the type with the literal tone union —
`Record<NonNullable<ToastProps['tone']>, string>` — or delete `TONE_TEXT` outright** if the class map
makes it redundant. Do not leave a dangling reference to `ColorTokens`: `tokens.ts` is deleted in Task 8,
so a leftover reference there turns `tsc -b` red one task later, where it will look like Task 8's fault.

- [ ] **Step 4: Run tests**

Run: `cd frontend && npx vitest run src/shared/ui && npm run build`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/shared/ui/Modal.tsx frontend/src/shared/ui/Modal.module.css frontend/src/shared/ui/Drawer.tsx frontend/src/shared/ui/Drawer.module.css frontend/src/shared/ui/Tooltip.tsx frontend/src/shared/ui/Tooltip.module.css frontend/src/shared/ui/Toast.tsx frontend/src/shared/ui/Toast.module.css
git commit -m "refactor(ui): move Modal, Drawer, Tooltip and Toast onto CSS Modules"
```

---

## Task 8: Table, Tree, Progress — and delete `tokens.ts`

**Files:**
- Create: `frontend/src/shared/ui/Table.module.css`, `Tree.module.css`, `Progress.module.css`
- Modify: `frontend/src/shared/ui/Table.tsx`, `Tree.tsx`, `Progress.tsx`, `frontend/src/shared/ui/index.ts`
- Delete: `frontend/src/shared/ui/tokens.ts`
- Test: `frontend/src/shared/ui/feedback-table.test.tsx`, `tree.test.tsx`

**Interfaces:**
- Consumes: tokens (Tasks 1–2).
- Produces: `Table`, `Tree`, `Progress` with unchanged props minus `theme`. `shared/ui/index.ts` no longer exports `colors`, `font`, `radius`, `spacing`, `UiTheme`.

- [ ] **Step 1: Run the existing tests**

Run: `cd frontend && npx vitest run src/shared/ui/feedback-table.test.tsx src/shared/ui/tree.test.tsx`
Expected: PASS. Note the Table's header/row assertions and whatever `tree.test.tsx` actually asserts.

**Correction to earlier plan text: this codebase's `Tree` has no keyboard navigation and no
`aria-level`.** An earlier draft of this step, and Step 3 below, told you to "keep every ARIA attribute
and the Tree's keyboard navigation untouched" and to look for `aria-level` assertions. Neither exists in
`Tree.tsx` or `tree.test.tsx` — there is no arrow/Home/End handling to preserve and no `aria-level` being
rendered. **Nothing is missing and nothing was removed.** Do not add keyboard navigation or `aria-level`
to satisfy the instruction: that would be a new feature, not this task, and no test asks for it. Read the
test file and preserve exactly what it does assert.

- [ ] **Step 2: Create the three modules**

```css
/* Table.module.css */
.table { width: 100%; border-collapse: collapse; font-size: var(--fs-md); }
.caption { text-align: left; color: var(--text-muted); font-size: var(--fs-sm); padding-bottom: var(--sp-2); }
.th {
  text-align: left;
  font-size: var(--fs-sm);
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase; /* interface label only */
  color: var(--text-muted);
  border-bottom: 1px solid var(--border-control);
  padding: var(--sp-2) var(--sp-3);
}
.td {
  color: var(--text);
  border-bottom: 1px solid var(--border-subtle); /* decorative separator, exempt from 1.4.11 */
  padding: var(--sp-2) var(--sp-3);
  vertical-align: top;
  min-width: 0;
}
.empty { color: var(--text-subtle); padding: var(--sp-4); text-align: center; }
```

```css
/* Tree.module.css */
.tree { font-size: var(--fs-md); }
.item {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  min-height: var(--density-row);
  padding: 0 var(--sp-2);
  color: var(--text);
  cursor: pointer;
  border-radius: var(--radius);
}
.item[aria-selected='true'] { background: var(--surface-raised); }
.item:focus-visible {
  outline: var(--focus-ring) solid var(--focus);
  outline-offset: calc(-1 * var(--focus-offset));
}
```

```css
/* Progress.module.css */
.track {
  background: var(--surface-raised);
  border: 1px solid var(--border-control);
  border-radius: var(--radius);
  height: 0.5rem;
  overflow: hidden;
}
.bar { background: var(--primary); height: 100%; }
.label { font-family: var(--font-mono); font-size: var(--fs-sm); color: var(--text-muted); }
```

- [ ] **Step 3: Rewrite the three components**

Swap inline styles for module classes. Keep every ARIA attribute untouched — see Step 1: there is no
Tree keyboard navigation and no `aria-level` in this codebase, so there is nothing of the sort to keep
or to add.

- [ ] **Step 4: Delete `tokens.ts` and trim `index.ts`**

Run first to prove nothing else needs it:

```bash
cd frontend && grep -rn "from './tokens'\|ui/tokens" src/ || echo "no importers left"
```

Expected: `no importers left`. Then delete the file and remove these two lines from `frontend/src/shared/ui/index.ts`:

```ts
export { colors, font, radius, spacing } from './tokens';
export type { UiTheme } from './tokens';
```

- [ ] **Step 5: Run the full suite and build**

Run: `cd frontend && npx vitest run && npm run build`
Expected: PASS. The contrast test now parses `tokens.css`, so deleting `tokens.ts` does not affect it.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/shared/ui/Table.tsx frontend/src/shared/ui/Table.module.css frontend/src/shared/ui/Tree.tsx frontend/src/shared/ui/Tree.module.css frontend/src/shared/ui/Progress.tsx frontend/src/shared/ui/Progress.module.css frontend/src/shared/ui/index.ts
git rm frontend/src/shared/ui/tokens.ts
git commit -m "refactor(ui): move Table, Tree and Progress onto CSS Modules; delete tokens.ts"
```

---

# G3 — Preferences reach the DOM

## Task 9: Appearance settings drive the DOM live

**Files:**
- Modify: `frontend/src/features/settings/AppearanceSettings.tsx`
- Modify: `frontend/src/features/settings/AppearanceSettings.test.tsx`
- Modify: `frontend/src/features/settings/themeRuntime.ts`
- Modify: `frontend/src/features/settings/themeRuntime.test.ts`
- Modify: `frontend/src/styles/base.css`
- Modify: `frontend/index.html`
- Test: `frontend/src/features/settings/AppearanceSettings.test.tsx`, `frontend/src/features/settings/themeRuntime.test.ts`

**Interfaces:**
- Consumes: `notifyPreferencesChanged`, `applyPreferences`, `UI_PREFERENCES_STORAGE_KEY` from `./themeRuntime`.
- Produces: saving or resetting in Appearance applies to the document immediately, **including `reduceMotion`**.

**Why this task carries `reduceMotion` too.** The plan first scoped it to "verified in Task 10", but
nothing ever wrote it to the DOM. `AppearanceSettings.tsx:129` renders the checkbox, `uiPreferences.ts`
sanitizes and persists the flag, and `applyPreferences()` wrote only `data-theme`, `data-density` and the
root font size; `base.css` honoured the **operating system's** `prefers-reduced-motion` and nothing else.
So a user who ticks "giảm chuyển động" saved a preference and got no behaviour change anywhere — a
functional gap, not a missing test. It closes here, where the preference is applied, and is proven here.
`reduceMotion` reaches the DOM as a root attribute, `data-reduce-motion`, matching the existing
`data-theme` / `data-density` pattern — `applyPreferences()` stays the only writer.

- [ ] **Step 1: Write the failing test for the runtime**

Append to `themeRuntime.test.ts`, inside the existing `describe('applyPreferences', …)` block. Its
`beforeEach` already strips the root attributes — add `removeAttribute('data-reduce-motion')` there
alongside the others:

```ts
  it('writes data-reduce-motion from the preference', () => {
    applyPreferences({ ...defaultPreferences(), reduceMotion: true });
    expect(document.documentElement.dataset.reduceMotion).toBe('true');

    applyPreferences({ ...defaultPreferences(), reduceMotion: false });
    expect(document.documentElement.dataset.reduceMotion).toBe('false');
  });
```

`false` must be written explicitly, not omitted: the attribute is what CSS keys off, and an absent
attribute is indistinguishable from a build that forgot to write it.

- [ ] **Step 2: Write the failing test for the settings screen**

Append to `AppearanceSettings.test.tsx`. It asserts the DOM actually changes, which is the behaviour that did not exist before this plan.

**Correction to earlier plan text for this step.** That file has ONE describe block, `describe('AppearanceSettings (U10)')` at `:13`, a top-level `afterEach` at `:8` (`cleanup()` + `window.localStorage.clear()`) and **no `beforeEach` at all**; nothing in the file calls `applyPreferences`. Append to the real block and do not create a second one.

**The test must render through `ThemeProvider`.** `render(<AppearanceSettings />)` on its own has no subscriber: `notifyPreferencesChanged()` dispatches `studio:ui-preferences` on `window`, and `ThemeProvider.tsx:16` is the only listener in the codebase. A bare render would therefore fail for a reason unrelated to the change. The provider's mount effect applies the stored document first — defaults, so `reduceMotion: false` → `data-reduce-motion="false"` — and then reacts to the event, which gives the test the known-`false` start it needs and makes the assertion a proof of the real wiring.

Also add `document.documentElement.removeAttribute('data-reduce-motion');` to the existing top-level `afterEach`: jsdom's document is shared by every test in the file, so without it the attribute leaks into the neighbours.

```tsx
import { ThemeProvider } from './ThemeProvider';

  it('applies the reduced-motion preference to the document', () => {
    render(
      <ThemeProvider>
        <AppearanceSettings />
      </ThemeProvider>,
    );

    // The provider applied the stored defaults on mount.
    expect(document.documentElement.dataset.reduceMotion).toBe('false');

    fireEvent.click(screen.getByLabelText('Giảm chuyển động'));
    fireEvent.click(screen.getByRole('button', { name: 'Lưu tùy chọn hiển thị' }));
    expect(document.documentElement.dataset.reduceMotion).toBe('true');
  });
```

The label and button strings above are the ones actually rendered (`AppearanceSettings.tsx:128` and `:137`) — confirm them against the component before using them, and never rename a label to make a test pass.

Read `AppearanceSettings.tsx` and use the checkbox's **actual** label text in the pattern — the one
above is a guess. Do not rename the label to make the test pass.

- [ ] **Step 3: Run both to verify they fail**

Run: `cd frontend && npx vitest run src/features/settings/themeRuntime.test.ts src/features/settings/AppearanceSettings.test.tsx`
Expected: FAIL — `data-theme` stays `'dark'` after saving `'light'`, and `dataset.reduceMotion` is `undefined`.

- [ ] **Step 4: Write the attribute in `applyPreferences`**

In `themeRuntime.ts`, alongside the existing writes:

```ts
  root.dataset.reduceMotion = preferences.reduceMotion ? 'true' : 'false';
```

- [ ] **Step 5: Make `base.css` honour the attribute**

`base.css` has one `@media (prefers-reduced-motion: reduce)` block. CSS has no way to fold a media
query and a selector into one rule, so the declaration block is duplicated verbatim — that duplication
is deliberate and standard for this pattern, not an oversight. Leave the existing media query exactly as
it is (it covers users who set the OS preference and never open Settings) and add this block
immediately after it:

```css
/*
 * The in-app preference. Independent of the media query above on purpose: a
 * user may want reduced motion in this app without changing their OS, and a
 * user whose OS asks for it must still get it with the preference off.
 */
:root[data-reduce-motion='true'] *,
:root[data-reduce-motion='true'] *::before,
:root[data-reduce-motion='true'] *::after {
  animation-duration: 0.01ms !important;
  animation-iteration-count: 1 !important;
  transition-duration: 0.01ms !important;
  scroll-behavior: auto !important;
}
```

- [ ] **Step 6: Write the attribute in the boot script**

`frontend/index.html` must set it too, for the same reason it resolves the theme: so the first paint is
already correct and React does not change it a frame later. Next to the existing `data-density` line:

```js
    root.dataset.reduceMotion = prefs.reduceMotion === true ? 'true' : 'false';
```

Then extend the boot-script parity test in `themeRuntime.test.ts`. The existing `runBootScript` returns
`{ theme, density, fontSize }` — add `reduceMotion` to that result and to the `BootResult` interface, then
compare boot against the app-side path for the same input, exactly as the theme cases do:

- `stored('dark', { reduceMotion: true })` → `'true'` on both sides
- `stored('dark', { reduceMotion: false })` → `'false'` on both sides
- an absent key, a non-boolean such as `'yes'`, and the `null` (nothing stored) case → `'false'` on both

The point is that neither implementation can drift alone; a case that only compares against a literal
does not do that.

**Two notes on the current shape of that harness.** `runBootScript` takes a third argument,
`options: { matchMedia?: boolean }`, used by the degenerate-window case — it was added after this
section was written. Extend the function; do not remove the parameter or rewrite its callers.

And `stored()` is declared `stored(theme: string | null, extra: Record<string, string> = {})`, so
`stored('dark', { reduceMotion: true })` as written above is a **type error**, not a runtime one —
esbuild strips types without checking them, so vitest would still pass while `tsc -b` failed the build,
and the failure would look unrelated. Widen `extra` to `Record<string, unknown>`; every existing
string-valued call site stays as it is.

- [ ] **Step 7: Call the runtime from `save()` and `reset()`**

In `AppearanceSettings.tsx`, add the import and call `notifyPreferencesChanged()` at the end of both handlers. The existing `save()` already re-serializes and validates; do not change that logic — it is the guard that keeps credentials out of browser storage.

```tsx
import { notifyPreferencesChanged } from './themeRuntime';

// inside save(), after localStorage.setItem and the state updates:
notifyPreferencesChanged();

// inside reset(), after localStorage.removeItem and the state resets:
notifyPreferencesChanged();
```

Drop the local `const STORAGE_KEY = 'studio.ui-preferences';` and use the imported `UI_PREFERENCES_STORAGE_KEY` so the key has one definition.

- [ ] **Step 8: Run tests**

Run: `cd frontend && npx vitest run src/features/settings && npm run build`
Expected: PASS, including the pre-existing `preferencesAreSafe` / rejected-key tests and every boot-script parity case (theme, density, font size and the new reduce-motion ones).

- [ ] **Step 9: Commit**

```bash
git add frontend/src/features/settings/AppearanceSettings.tsx frontend/src/features/settings/AppearanceSettings.test.tsx frontend/src/features/settings/themeRuntime.ts frontend/src/features/settings/themeRuntime.test.ts frontend/src/styles/base.css frontend/index.html
git commit -m "feat(settings): make the appearance controls apply to the document immediately"
```

---

## Task 10: Verify density, fontScale and reduceMotion end to end

**Files:**
- Test: `frontend/e2e/visual-a11y.spec.ts` (extend)

**Interfaces:**
- Consumes: everything from Tasks 1–9.
- Produces: a browser-level proof that the preferences reach the DOM, which no unit test can give.

- [ ] **Step 1: Read the existing spec**

Run: `cd frontend && npx playwright test e2e/visual-a11y.spec.ts`
Expected: PASS on the existing reflow/200%/focus/Viet-CJK cases. This is also the baseline the G4 refactor is compared against — record the pass count.

- [ ] **Step 2: Add the preference cases**

Append to `visual-a11y.spec.ts`:

```ts
test('appearance preferences reach the document', async ({ page }) => {
  await page.goto('/');
  await page.evaluate(() => window.localStorage.clear());
  await page.reload();

  // Dark is the default, regardless of the host OS preference.
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');

  await page.goto('/settings/appearance');
  await page.getByLabel('Theme').selectOption('light');
  await page.getByRole('button', { name: 'Lưu tùy chọn hiển thị' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');

  await page.getByLabel('Cỡ chữ').selectOption('large');
  await page.getByRole('button', { name: 'Lưu tùy chọn hiển thị' }).click();
  // fontScale reaches the DOM as the ROOT FONT SIZE, not as density. An earlier
  // draft asserted data-density here, which was decorative: it is 'comfortable'
  // or 'compact' no matter what the font select did, so the assertion passed
  // even if the save wrote nothing. 112.5% is ROOT_FONT_SIZE.large
  // (themeRuntime.ts) — assert the value applyPreferences actually writes.
  await expect(page.locator('html')).toHaveAttribute('style', /font-size:\s*112\.5%/);
});

test('system theme follows the OS and is overridden by an explicit choice', async ({ browser }) => {
  const context = await browser.newContext({ colorScheme: 'light' });
  const page = await context.newPage();
  await page.goto('/settings/appearance');
  await page.getByLabel('Theme').selectOption('system');
  await page.getByRole('button', { name: 'Lưu tùy chọn hiển thị' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');

  await page.goto('/settings/appearance');
  await page.getByLabel('Theme').selectOption('dark');
  await page.getByRole('button', { name: 'Lưu tùy chọn hiển thị' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await context.close();
});
```

**Corrected labels and button names.** The four labels `AppearanceSettings` actually renders are
`Theme`, `Mật độ hiển thị`, `Cỡ chữ` and `Giảm chuyển động` (`AppearanceSettings.tsx:76,92,110,128`);
its buttons are `Lưu tùy chọn hiển thị` and `Về mặc định`. Earlier plan text used loose regexes
(`/chủ đề|theme/i`, `/lưu/i`) — the patterns above now use the exact strings. Read the component and
confirm before running, and do not rename any label to make the test pass.

Then add the in-app reduced-motion case. This one is deliberately **not** the same as Task 24's
`reducedMotion: 'reduce'` case: that one exercises the OS media query, this one proves the stored
preference works on its own. Run it in a context that explicitly asks for **no** reduced motion, or the
two causes are indistinguishable and the test would pass even if the preference wrote nothing:

```ts
test('the in-app reduced-motion preference stops transitions on its own', async ({ browser }) => {
  // Explicitly no OS-level reduced motion: the only thing that can stop the
  // transitions below is the preference written to data-reduce-motion.
  const context = await browser.newContext({ reducedMotion: 'no-preference' });
  const page = await context.newPage();

  await page.goto('/settings/appearance');
  await page.getByLabel('Giảm chuyển động').check();
  await page.getByRole('button', { name: 'Lưu tùy chọn hiển thị' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-reduce-motion', 'true');

  // Every transition must be neutralised. Collect the offenders rather than
  // counting them, so a failure names the rule that leaked.
  const offenders = await page.evaluate(() =>
    Array.from(document.querySelectorAll<HTMLElement>('*'))
      .map((el) => ({
        name: `${el.tagName.toLowerCase()}${el.className ? `.${String(el.className).split(' ')[0]}` : ''}`,
        duration: window.getComputedStyle(el).transitionDuration,
      }))
      .filter((entry) => entry.duration !== '0s' && entry.duration !== '0.01ms')
      .map((entry) => `${entry.name} [${entry.duration}]`),
  );
  expect(offenders, `still transitioning: ${offenders.slice(0, 5).join(' | ')}`).toEqual([]);

  // And it must survive a reload, i.e. the boot script writes it too — not just
  // the React effect that ran after the first mount.
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-reduce-motion', 'true');

  await context.close();
});
```

Read the checkbox's actual label in `AppearanceSettings.tsx` and use its exact text; the pattern above
is a guess. Use `.check()` only if the control is a checkbox — if it is a `<Select>` or a button, use the
matching interaction instead. Do not rename the label to make the test pass.

If the reload assertion fails while the first one passes, that is a real finding: it means `index.html`'s
boot script is not writing the attribute, so the preference is lost on every page load until React mounts.

- [ ] **Step 3: Run the spec**

Run: `cd frontend && npx playwright test e2e/visual-a11y.spec.ts`
Expected: PASS, including the three new cases.

- [ ] **Step 4: Commit**

```bash
git add frontend/e2e/visual-a11y.spec.ts
git commit -m "test(e2e): prove appearance preferences reach the document, not just localStorage"
```

---

# G4 — Router split

Every task in G4 is **behaviour-neutral**: no visual change, no logic change. The gate is that the 9 e2e specs pass identically before and after. Run them before starting.

- [ ] **Record the e2e baseline**

Run: `cd frontend && npx playwright test`
Expected: PASS. Write the pass count down; every G4 task compares against it.

## G4's transitional `styles` rule (applies to Tasks 11–15)

`router.tsx` defines ONE module-local, unexported `const styles: Record<string, React.CSSProperties>`
(lines 1245–1374 at the start of G4) that every screen reads through `style={styles.<key>}`. It is not
exported, so a moved screen cannot import it back — and importing it would be circular anyway, since
`router.tsx` now imports the screen.

**So each extracted module carries its own local `const styles` holding exactly the keys its moved
range referenced, and nothing else.** Measure, do not guess:

```bash
# over the range you are moving, e.g. 129-177 for Shell:
awk 'NR>=129 && NR<=177' src/routes/router.tsx | grep -o "styles\.[A-Za-z0-9_]*" | sort -u
```

Measured key counts, so a mismatch is visible immediately:

| Module | Task | Keys |
|---|---|---|
| `Shell` | 11 | 3 |
| `ImportScreen` | 12 | 8 |
| `BatchScreen` | 12 | 0 (no `styles` object at all) |
| `TranslationScreen` | 13 | 12 |
| `VoiceScreen` | 14 | 5 |
| `AudioScreen` | 14 | 7 |
| `ExportScreen` | 15 | 1 |
| `JobsScreen` | 15 | 2 |
| `BilingualScreen` | 15 | 1 |
| `JobDraftScreen` | 15 | 2 |
| `jobActions` | 15 | 0 (returns strings, not elements) |

If your grep disagrees with a number here, the number you got is right and this table is wrong —
report the discrepancy rather than forcing a match.

Duplication across modules is accepted on purpose: the object is transitional. G5 replaces every one of
these inline styles with a CSS module, and Task 16 deletes what is left in `router.tsx`. Do not delete
the object from `router.tsx` during Tasks 11–15, and do not try to make it shared.

## Task 11: Extract `Shell` and the navigation

**Files:**
- Create: `frontend/src/routes/Shell.tsx`
- Modify: `frontend/src/routes/router.tsx`
- Test: `frontend/e2e/workspace-tabs.spec.ts` (existing)

**Interfaces:**
- Consumes: `GlobalNav`, `ProjectNav`, `WorkspaceTabs` (existing components).
- Produces: `export function Shell()` from `routes/Shell.tsx`, consumed by `router.tsx`.

- [ ] **Step 1: Move the `Shell` function verbatim**

Cut lines 129–177 of `router.tsx` into `frontend/src/routes/Shell.tsx`. Change nothing inside the body. In particular keep **both** `useMatch` calls unconditional — they were `useMatch(a) ?? useMatch(b)` and that changing hook count crashed the app (fixed in `171d2d6`):

```tsx
const projectMatch = useMatch('/projects/:projectId/*');
const chapterMatch = useMatch('/chapters/:chapterId/*');
```

Export `Shell` and import it in `router.tsx`. Move only the imports `Shell` actually needs.

- [ ] **Step 2: Run the tests that cover the shell**

Run: `cd frontend && npx vitest run && npx playwright test e2e/workspace-tabs.spec.ts e2e/import-preview.spec.ts`
Expected: PASS, same count as baseline.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/routes/Shell.tsx frontend/src/routes/router.tsx
git commit -m "refactor(routes): extract Shell into its own module, behaviour unchanged"
```

---

## Task 12: Extract Import and Batch screens

**Files:**
- Create: `frontend/src/routes/screens/ImportScreen.tsx`, `BatchScreen.tsx`
- Modify: `frontend/src/routes/router.tsx`
- Test: `frontend/e2e/import-preview.spec.ts`, `frontend/e2e/batch-recovery.spec.ts`

**Interfaces:**
- Consumes: `Shell` (Task 11).
- Produces: `ImportScreen`, `BatchScreen` exported from `routes/screens/`.

- [ ] **Step 1: Move both screens verbatim**

Cut `ImportScreen` (lines 178–311) and `BatchScreen` (312–323) into their own files. Keep every `data-testid`, every string, and every prop. Move the imports each one needs; leave the rest in `router.tsx`.

**Also move `type Chapter` (lines 30–37) into `ImportScreen.tsx`.** It is used at exactly one place, `router.tsx:246`, inside `ImportScreen`. Left behind it would have to be imported back out of `router.tsx`, which now imports `ImportScreen` — the `import type` is erased at compile time so it would not break at runtime, but it leaves router.tsx carrying a declaration nothing there uses, and Task 16 requires router.tsx to end up as the route table and nothing else. `BatchScreen` uses none of the module-local types.

- [ ] **Step 2: Run the covering specs**

Run: `cd frontend && npx vitest run && npx playwright test e2e/import-preview.spec.ts e2e/batch-recovery.spec.ts`
Expected: PASS, same as baseline.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/routes/screens/ImportScreen.tsx frontend/src/routes/screens/BatchScreen.tsx frontend/src/routes/router.tsx
git commit -m "refactor(routes): extract ImportScreen and BatchScreen, behaviour unchanged"
```

---

## Task 13: Extract the Translation screen

**Files:**
- Create: `frontend/src/routes/screens/TranslationScreen.tsx`
- Modify: `frontend/src/routes/router.tsx`
- Test: `frontend/e2e/multivoice-cloud.spec.ts`, `frontend/src/App.test.tsx`

**Interfaces:**
- Consumes: `Shell` (Task 11).
- Produces: `TranslationScreen` exported from `routes/screens/`.

The largest screen (lines 324–931). Move it whole; do not split it further in this task — a behaviour-neutral move is the point, and splitting while moving makes a failure ambiguous.

- [ ] **Step 1: Move verbatim**

Cut lines 324–931 into `frontend/src/routes/screens/TranslationScreen.tsx`.

**Also move `type TranslationPayload` (lines 49–61) and `type TranslationIssue` (39–47) into `TranslationScreen.tsx`.** `TranslationPayload` is referenced only inside this screen — `:327,347,374,423,449,474` — and it is the only user of `TranslationIssue`, at `:60`. Same reasoning as Task 12: leaving them in `router.tsx` forces a back-import from a module that now imports this one, and router.tsx is supposed to end up as the route table and nothing else.

Preserve exactly, because tests assert on them:
- `TRANSLATION_QA_BLOCKERS_OPEN` and the approval guard that uses it.
- The `QualityPlanPanel` mount conditional (only when a cloud profile is present).
- `data-testid` attributes.
- Every user-visible string.

- [ ] **Step 2: Run the covering tests**

Run: `cd frontend && npx vitest run src/App.test.tsx && npx playwright test e2e/multivoice-cloud.spec.ts`
Expected: PASS. `App.test.tsx` is the strongest signal here — it drives the whole translation flow.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/routes/screens/TranslationScreen.tsx frontend/src/routes/router.tsx
git commit -m "refactor(routes): extract TranslationScreen, behaviour unchanged"
```

---

## Task 14: Extract Voice and Audio screens

**Files:**
- Create: `frontend/src/routes/screens/VoiceScreen.tsx`, `AudioScreen.tsx`
- Modify: `frontend/src/routes/router.tsx`
- Test: `frontend/e2e/single-voice.spec.ts`, `frontend/e2e/audio-range.spec.ts`

**Interfaces:**
- Consumes: `Shell` (Task 11).
- Produces: `VoiceScreen`, `AudioScreen` exported from `routes/screens/`.

- [ ] **Step 1: Move both verbatim**

**Corrected ranges — earlier plan text said `VoiceScreen` is lines 932–1030.** `VoiceScreen`'s closing brace is at line **1015**. Lines 1016–1029 are two declarations that belong to `AudioScreen`: `type ServerMaster` (1016–1020) and `STALE_MASTER_CODES` (1022–1029). Cutting 932–1030 as written would move both into `VoiceScreen.tsx` while the bullet below still demands `STALE_MASTER_CODES` be preserved as `AudioScreen`'s — leaving `AudioScreen.tsx` referencing two out-of-scope identifiers, for a `tsc -b` failure the implementer would then improvise around.

| What | Lines | Goes to |
|---|---|---|
| `VoiceScreen` | 932–1015 | `VoiceScreen.tsx` |
| `type ServerMaster` | 1016–1020 | `AudioScreen.tsx` |
| `STALE_MASTER_CODES` | 1022–1029 | `AudioScreen.tsx` |
| `AudioScreen` | 1031–1172 | `AudioScreen.tsx` |

Also move in this task, both used **only** by `VoiceScreen` (`router.tsx:937,941`): `const fakePresetId` (line 27) and `const fakeAudioEnabled` (line 28). They are runtime values — left behind, `VoiceScreen.tsx` would import them back out of `router.tsx`, a real circular import, since `router.tsx` imports `VoiceScreen`. Types survive that (erased at compile time); runtime consts do not.

`RenderedAudio` (line 63) is the one declaration here used by **both** screens — `VoiceScreen:986` produces it, `AudioScreen:1035–1036` consumes it. Put it in `AudioScreen.tsx` and `import type { RenderedAudio } from './AudioScreen'` in `VoiceScreen.tsx`.

The rest have exactly one user each: `type VoiceCatalogPayload` (90–99) is used at `:945` in `VoiceScreen`; `type AudioStatus` (70–75) is used at `:1055` and `:1096` in `AudioScreen`. Each moves with its screen.

`GateDecision` (77–81) and `ExportBundle` (83–88) are declared and never referenced — module-local, unexported, dead. Leave them; Task 16 deletes what remains.

Preserve exactly:
- `AudioScreen`'s `STALE_MASTER_CODES` set — `MASTER_HASH_MISMATCH`, `MASTER_ARTIFACT_NOT_READY`, `MASTER_TRANSLATION_STALE`, `MASTER_VOICE_PLAN_STALE`, `MASTER_PROBE_HASH_MISMATCH` — and the check that blocks approval when the server master differs from the one being auditioned (A05).
- `data-testid="master-audio"`.
- `ArtifactPlayer` wiring and the `AudioScreen` deep-link master load.
- Every user-visible string and every `data-testid`.

- [ ] **Step 2: Run the covering specs**

Run: `cd frontend && npx vitest run && npx playwright test e2e/single-voice.spec.ts e2e/audio-range.spec.ts`
Expected: PASS, including the approve-with-stale-hash case that must return 409.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/routes/screens/VoiceScreen.tsx frontend/src/routes/screens/AudioScreen.tsx frontend/src/routes/router.tsx
git commit -m "refactor(routes): extract VoiceScreen and AudioScreen, behaviour unchanged"
```

---

## Task 15: Extract the remaining screens and `jobActions`

**Files:**
- Create: `frontend/src/routes/screens/ExportScreen.tsx`, `JobsScreen.tsx`, `BilingualScreen.tsx`, `JobDraftScreen.tsx`, `frontend/src/routes/jobActions.ts`
- Modify: `frontend/src/routes/router.tsx`
- Test: `frontend/e2e/export-review.spec.ts`, `frontend/e2e/provider-credentials.spec.ts`

**Interfaces:**
- Consumes: `Shell` (Task 11), `defaultJobEventStore` (existing).
- Produces: `jobActions` exported from `routes/jobActions.ts`; the four screens exported from `routes/screens/`.

- [ ] **Step 1: Move the four screens and the helper verbatim**

`ExportScreen` lines 1173–1193, `jobActions` 1194–1205, `JobsScreen` 1206–1216, `BilingualScreen` 1217–1232, `JobDraftScreen` 1233–1244. Preserve:
- `data-testid="export-workflow"` and `data-testid="manifest-export-private-1"`.
- The `jobActions()` shape — `JobsScreen` consumes it; `BatchQueue` deliberately does **not** (it POSTs the routes itself so a rejection reason stays visible). Do not "unify" them.

- [ ] **Step 2: Run the covering specs**

Run: `cd frontend && npx vitest run && npx playwright test e2e/export-review.spec.ts e2e/provider-credentials.spec.ts`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/routes/screens/ExportScreen.tsx frontend/src/routes/screens/JobsScreen.tsx frontend/src/routes/screens/BilingualScreen.tsx frontend/src/routes/screens/JobDraftScreen.tsx frontend/src/routes/jobActions.ts frontend/src/routes/router.tsx
git commit -m "refactor(routes): extract the remaining screens and jobActions, behaviour unchanged"
```

---

## Task 16: `router.tsx` becomes the route table, and the inline style object dies

**Files:**
- Modify: `frontend/src/routes/router.tsx`
- Delete: the `styles` object (lines 1245–1374 at the start of G4)
- Test: the full suite

**Interfaces:**
- Consumes: all extracted screens.
- Produces: `router.tsx` under ~120 lines containing the route tree and nothing else.

- [ ] **Step 1: Confirm nothing still reads the `styles` object**

```bash
cd frontend && grep -rn "styles\." src/routes/router.tsx
```

Every hit must be inside the `styles` object definition itself. Any hit inside a screen means that screen was not fully moved — go back to its task.

- [ ] **Step 2: Delete the object**

Remove the entire `styles` object from `router.tsx`. This removes the last hardcoded palette **in this
file**, including the `linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%)` button fill.

It does **not** remove the last hardcoded palette in the app: Tasks 11–15 gave each extracted screen a
local copy of the keys it used (G4's transitional rule), so those hex values now live in
`src/routes/screens/*.tsx`. G5 is what converts them. Do not chase them here.

If some screen still references `styles.<key>`, that screen was not fully moved. Do not leave a hex literal behind and do not delete the object: go back to that screen's G4 task, finish moving it into its own module, then continue here. A leftover hex literal would pass Step 2 and fail Step 3's grep, so leaving one must be impossible.

- [ ] **Step 3: Verify**

Run: `cd frontend && npx vitest run && npm run build && npx playwright test`

And confirm `router.tsx` itself is clean — **scoped to this one file, not the repo**:

```bash
cd frontend && grep -nE "#[0-9a-fA-F]{3,8}" src/routes/router.tsx
```

Expected: no hits.

**Do not run a repo-wide version of that grep here, and do not treat a repo-wide hit count as a
failure.** At this point in the plan 42 files still carry raw hex (~500 occurrences; the largest are
`WenkuImport.tsx`, `BatchQueue.tsx`, `ProjectWizard.tsx`, `ExportWorkflow.tsx`, plus the
`src/routes/screens/*.tsx` G4 just created). Converting them is the entire job of G5, which runs
**after** this task. The repo-wide "one colour system" grep can only pass at the very end of G5 — it
belongs to the final gate, not to Task 16.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/routes/router.tsx
git commit -m "refactor(routes): reduce router.tsx to the route table and delete the inline style object"
```

---

# G5 — The visual

Each task applies the token system and the Conventions to one area. The rules are the same everywhere:

- Layout with grid/flex; `min-width: 0` on any grid child that can hold long prose.
- Panels separated by `1px solid var(--border-subtle)`; control outlines by `1px solid var(--border-control)`.
- `border-radius: var(--radius)` (zero) on everything.
- Identifiers, counts, costs and diagnostics in `var(--font-mono)`. Never prose.
- Interface labels may be uppercase; source and translated text never.
- Status is carried by text or an icon, never by colour alone.

## Task 17: Global navigation and the settings shell

**Files:**
- Create: `frontend/src/features/workspace/GlobalNav.module.css`, `ProjectNav.module.css`, `WorkspaceTabs.module.css`
- Modify: `GlobalNav.tsx`, `ProjectNav.tsx`, `WorkspaceTabs.tsx`, `SettingsLayout.tsx`
- Test: `frontend/src/features/workspace/global-nav.test.tsx`, `ProjectNav.test.tsx`, `WorkspaceTabs.test.tsx`

**Interfaces:**
- Consumes: tokens, Conventions.
- Produces: the nav surfaces on CSS Modules, still rendering the same text and `aria-current`.

- [ ] **Step 1: Run the three nav tests to fix the contract**

Run: `cd frontend && npx vitest run src/features/workspace`
Expected: PASS. The three areas are real links with `aria-current`; the tablist keeps roving tabindex and `←`/`→`/`Home`/`End`/`Enter`/`Delete`.

- [ ] **Step 2: Write `GlobalNav.module.css`**

```css
.nav {
  display: flex;
  flex-wrap: wrap; /* the 320px overflow fix from 149c729 */
  gap: 0;
  background: var(--bg);
  border-bottom: 1px solid var(--border-control);
  padding: 0 var(--sp-4);
  min-width: 0;
}
.link {
  font-size: var(--fs-sm);
  font-weight: 500;
  letter-spacing: 0.06em;
  text-transform: uppercase; /* interface label */
  color: var(--text-muted);
  text-decoration: none;
  padding: var(--sp-3) var(--sp-4);
  border-bottom: 2px solid transparent;
}
.link:hover { color: var(--text); background: var(--surface); }
.link[aria-current='page'] { color: var(--text); border-bottom-color: var(--primary); }
.link:focus-visible { outline: var(--focus-ring) solid var(--focus); outline-offset: calc(-1 * var(--focus-offset)); }
```

- [ ] **Step 3: Write `ProjectNav.module.css` and `WorkspaceTabs.module.css`**

```css
/* ProjectNav.module.css */
.wrapper { background: var(--surface); border-bottom: 1px solid var(--border-subtle); padding: var(--sp-2) var(--sp-4); min-width: 0; }
.breadcrumb { font-family: var(--font-mono); font-size: var(--fs-sm); color: var(--text-subtle); margin-bottom: var(--sp-2); }
.areas { display: flex; flex-wrap: wrap; gap: var(--sp-4); }
.area { font-size: var(--fs-sm); color: var(--text-muted); text-decoration: none; padding-bottom: var(--sp-1); border-bottom: 2px solid transparent; }
.area:hover { color: var(--text); }
.area[aria-current='page'] { color: var(--text); border-bottom-color: var(--primary); }
.area:focus-visible { outline: var(--focus-ring) solid var(--focus); outline-offset: var(--focus-offset); }
```

```css
/* WorkspaceTabs.module.css */
.tablist { display: flex; align-items: stretch; background: var(--surface); border-bottom: 1px solid var(--border-control); overflow-x: auto; min-width: 0; }
.tab {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-2);
  max-width: 14rem;
  background: transparent;
  border: none;
  border-right: 1px solid var(--border-subtle);
  border-bottom: 2px solid transparent;
  color: var(--text-muted);
  font-size: var(--fs-sm);
  padding: var(--sp-2) var(--sp-3);
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.tab[aria-selected='true'] { background: var(--bg); color: var(--text); border-bottom-color: var(--primary); }
.tab:focus-visible { outline: var(--focus-ring) solid var(--focus); outline-offset: calc(-1 * var(--focus-offset)); }
.dirty { color: var(--warning); } /* always paired with a text label, never colour alone */
.close { font-family: var(--font-mono); color: var(--text-subtle); padding: 0 var(--sp-1); }
```

- [ ] **Step 4: Apply the modules and run the tests**

Run: `cd frontend && npx vitest run src/features/workspace && npx playwright test e2e/workspace-tabs.spec.ts`
Expected: PASS. Do not touch the roving-tabindex effect's dependencies — the `←`-then-`Enter` regression from `8444c1b` lives there.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/workspace/*.module.css frontend/src/features/workspace/GlobalNav.tsx frontend/src/features/workspace/ProjectNav.tsx frontend/src/features/workspace/WorkspaceTabs.tsx frontend/src/features/settings/SettingsLayout.tsx
git commit -m "feat(ui): restyle global nav, project nav and workspace tabs"
```

---

## Task 18: Library and Jobs — the dense console surfaces

**Files:**
- Create: `frontend/src/routes/screens/LibraryScreen.module.css` (the library list lives in `Shell` or a screen; put the module beside whichever file renders it — confirm with `grep -rn "Tải thêm" frontend/src`)
- Modify: the library list component, `JobsScreen.tsx`, `JobProgress`, `JobsList`, `BatchQueue`
- Test: `frontend/src/App.test.tsx`, `frontend/e2e/batch-recovery.spec.ts`

**Interfaces:**
- Consumes: `Table`, `Progress`, `Button` primitives.
- Produces: library rows and job rows on the token system.

- [ ] **Step 1: Write the library module**

```css
.rows { border-top: 1px solid var(--border-control); }
.row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto; /* minmax(0,1fr): the 320px fix */
  align-items: center;
  gap: var(--sp-3);
  min-height: var(--density-row);
  padding: var(--density-pad) var(--sp-4);
  border-bottom: 1px solid var(--border-subtle);
}
.row:hover { background: var(--surface); }
.title { color: var(--text); font-size: var(--fs-md); min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.meta { font-family: var(--font-mono); font-size: var(--fs-sm); color: var(--text-subtle); }
.search { display: flex; gap: var(--sp-2); align-items: flex-end; padding: var(--sp-4); min-width: 0; }
.pager { display: flex; align-items: center; justify-content: space-between; gap: var(--sp-3); padding: var(--sp-3) var(--sp-4); }
.pageCount { font-family: var(--font-mono); font-size: var(--fs-sm); color: var(--text-muted); }
```

- [ ] **Step 2: Write the job-row module**

```css
.job {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: var(--sp-3);
  min-height: var(--density-row);
  padding: var(--density-pad) var(--sp-4);
  border-bottom: 1px solid var(--border-subtle);
}
.state { font-size: var(--fs-sm); font-weight: 500; letter-spacing: 0.04em; text-transform: uppercase; }
.stateRunning { color: var(--text); }
.stateFailed { color: var(--danger); }
.stateDone { color: var(--success); }
.stateCanceled { color: var(--text-subtle); }
.jobId { font-family: var(--font-mono); font-size: var(--fs-sm); color: var(--text-subtle); }
.actions { display: flex; gap: var(--sp-2); }
```

Every `.state*` class is accompanied by the Vietnamese state label already rendered — colour is never the only signal.

- [ ] **Step 3: Apply, keeping every string and testid**

Do not change `'Chưa có job đang chạy'`, `'Hủy job'`, `'Thử lại'`, `'Đã hủy'`, `'Đang chờ'`, `'Tải thêm'`, the `n/total` page counter, or `retryableFailures()` gating. `retryableFailures()` is an allowlist matching the backend classifier — do not widen it.

- [ ] **Step 4: Run**

Run: `cd frontend && npx vitest run && npx playwright test e2e/batch-recovery.spec.ts e2e/visual-a11y.spec.ts`
Expected: PASS, including the 320/390 reflow cases.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/screens/ frontend/src/features/workspace/
git commit -m "feat(ui): restyle the library list and job rows as dense console surfaces"
```

---

## Task 19: Import review and batch queue

**Files:**
- Create: modules beside `ImportScreen.tsx`, `BatchScreen.tsx`, `ImportPreview`, `BatchQueue`
- Modify: those components
- Test: `frontend/e2e/import-preview.spec.ts`, `frontend/src/features/import/ImportPreview.test.tsx`

**Interfaces:**
- Consumes: tokens, `Table`, `Button`, `Toast`.
- Produces: the import candidate table and the batch progress table.

- [ ] **Step 1: Write the import candidate module**

```css
.section { padding: var(--sp-4); border-bottom: 1px solid var(--border-subtle); min-width: 0; }
.sectionTitle { font-size: var(--fs-sm); font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: var(--text-muted); margin-bottom: var(--sp-2); }
.candidate { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: var(--sp-3); align-items: baseline; padding: var(--sp-2) var(--sp-3); border-bottom: 1px solid var(--border-subtle); min-width: 0; }
.ordinal { font-family: var(--font-mono); font-size: var(--fs-sm); color: var(--text-subtle); }
.sourcePath { font-family: var(--font-mono); font-size: var(--fs-sm); color: var(--text-muted); overflow-wrap: anywhere; }
.warning { color: var(--warning); font-size: var(--fs-sm); display: flex; gap: var(--sp-1); align-items: baseline; }
```

`DUPLICATE_ORDINAL` must stay attached to **every** duplicate file and name its own `sourcePath` — `import-preview.spec.ts` asserts exactly that with two files sharing chapter 1.

- [ ] **Step 2: Write the batch queue module**

```css
.table { width: 100%; border-collapse: collapse; font-size: var(--fs-md); }
.summary { padding: var(--sp-3) var(--sp-4); border-bottom: 1px solid var(--border-control); display: flex; justify-content: space-between; gap: var(--sp-3); align-items: center; }
.failure { color: var(--danger); font-size: var(--fs-sm); }
.notRetryable { color: var(--text-subtle); font-size: var(--fs-sm); }
```

- [ ] **Step 3: Apply**

Keep the `role="alert"` failure summary (“N/M job thất bại”), the per-job error codes, the “không thể tự thử lại” marker, and the single “Thử lại tất cả lỗi retryable” button driven by `retryableFailures()`. Keep `BatchQueue` POSTing the cancel/retry routes itself.

- [ ] **Step 4: Run**

Run: `cd frontend && npx vitest run && npx playwright test e2e/import-preview.spec.ts e2e/batch-recovery.spec.ts`
Expected: PASS, including the “3 files ⇒ 2 chapters” assertion.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/screens/ frontend/src/features/import/ frontend/src/features/jobs/
git commit -m "feat(ui): restyle import review and the batch queue"
```

---

## Task 20: The translation workspace — the locked three-pane IA

**Files:**
- Create: `TranslationScreen.module.css` + modules for `BilingualEditor`, `ContextInspector`, `RepairDiff`, `QualityPlanPanel`, `JobDraftPanel`
- Modify: those components
- Test: `frontend/src/App.test.tsx`, `frontend/src/features/translation/*.test.tsx`

**Interfaces:**
- Consumes: every primitive.
- Produces: the workspace at the locked geometry — navigator 240px, inspector 320px, split clamp 25–75%.

- [ ] **Step 1: Write the workspace shell module**

This is the locked IA from `docs/specs/ui-ux-spec.md`. The numbers are not negotiable.

```css
.workspace {
  display: grid;
  grid-template-columns: 240px minmax(0, 1fr) 320px;
  gap: 0;
  height: 100%;
  min-height: 0;
  background: var(--bg);
}
.navigator { border-right: 1px solid var(--border-subtle); overflow-y: auto; min-width: 0; }
.editor { min-width: 0; overflow-y: auto; display: flex; flex-direction: column; }
.inspector { border-left: 1px solid var(--border-subtle); overflow-y: auto; min-width: 0; }
.split { display: grid; grid-template-columns: clamp(25%, var(--split, 50%), 75%) minmax(0, 1fr); min-height: 0; flex: 1; }
.pane { min-width: 0; overflow-y: auto; padding: var(--sp-4); }
.paneSource { border-right: 1px solid var(--border-subtle); color: var(--text-muted); }

/* Below 1024px: inspector becomes a drawer, source/translation become tabs. */
@media (max-width: 1023px) {
  .workspace { grid-template-columns: minmax(0, 1fr); }
  .navigator, .inspector { display: none; }
  .split { grid-template-columns: minmax(0, 1fr); }
}
```

- [ ] **Step 2: Write the segment row module**

```css
.segment { display: grid; grid-template-columns: 3rem minmax(0, 1fr); border-bottom: 1px solid var(--border-subtle); min-width: 0; }
.segmentIndex { font-family: var(--font-mono); font-size: var(--fs-sm); color: var(--text-subtle); padding: var(--sp-2); border-right: 1px solid var(--border-subtle); text-align: right; }
.segmentBody { padding: var(--sp-3); min-width: 0; }
.source { font-family: var(--font-read); font-size: var(--fs-read); line-height: var(--lh-read); color: var(--text-muted); text-transform: none; }
.target { font-family: var(--font-read); font-size: var(--fs-read); line-height: var(--lh-read); color: var(--text); background: transparent; border: 1px solid transparent; width: 100%; resize: vertical; text-transform: none; }
.target:focus-visible { outline: var(--focus-ring) solid var(--focus); outline-offset: var(--focus-offset); border-color: var(--border-control); }
.revealed { box-shadow: inset 2px 0 0 0 var(--primary); }
.severity { font-size: var(--fs-sm); display: inline-flex; gap: var(--sp-1); align-items: center; }
.severityCritical { color: var(--danger); }
.severityMajor { color: var(--warning); }
.severityMinor { color: var(--text-subtle); }
```

`.source` and `.target` both set `text-transform: none` explicitly — the source pane holds CJK and the target holds Vietnamese, and an inherited uppercase would corrupt both.

- [ ] **Step 3: Write the inspector module**

```css
.group { border-bottom: 1px solid var(--border-subtle); padding: var(--sp-3) var(--sp-4); min-width: 0; }
.groupTitle { font-size: var(--fs-sm); font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: var(--text-muted); margin-bottom: var(--sp-2); }
.entry { font-size: var(--fs-md); color: var(--text); padding: var(--sp-1) 0; min-width: 0; overflow-wrap: anywhere; }
.stale { color: var(--warning); font-size: var(--fs-sm); }
.rawId { font-family: var(--font-mono); font-size: var(--fs-sm); color: var(--text-subtle); }
```

Raw diagnostic IDs stay inside the detail drawer only — the screen header shows human copy, per `ui-ux-spec.md`.

- [ ] **Step 4: Apply, preserving every guard**

Do not touch:
- `ImmE_COMPOSITION_ACTIVE` blocking save during composition. This is the CJK input path — get it wrong and typing Chinese breaks.
- `Ctrl/Cmd+S` saving the active segment with `expectedRunHash`.
- `CRITICAL` issues blocking approval, with the deliberate `force: true` bypass.
- The 409 path keeping unsaved text and showing the error code.
- `data-revealed` and the `scrollIntoView` QA reveal.
- `Confirm` applying a repair to the local draft only; “Áp dụng đề xuất” calling apply once with `proposalId` + `expectedProposalHash`.
- `RepairDiff`'s offline-generator warning staying visible above the accept button.

- [ ] **Step 5: Run**

Run: `cd frontend && npx vitest run && npx playwright test e2e/multivoice-cloud.spec.ts e2e/visual-a11y.spec.ts`
Expected: PASS, including the 1024/1366 reflow cases and the CJK/Vietnamese no-mojibake case.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/screens/TranslationScreen.module.css frontend/src/features/translation/
git commit -m "feat(ui): build the translation workspace at the locked three-pane geometry"
```

---

## Task 21: Voice and Audio

**Files:**
- Create: modules for `VoiceScreen`, `AudioScreen`, `VoicePreviewPanel`, `VoiceBrowser`, `ArtifactPlayer`
- Modify: those components
- Test: `frontend/e2e/single-voice.spec.ts`, `frontend/e2e/audio-range.spec.ts`, `frontend/src/features/voices/*.test.tsx`

**Interfaces:**
- Consumes: `Table`, `Button`, `Progress`, `Toast`.
- Produces: the voice list, preview panel and artifact player on the token system.

- [ ] **Step 1: Write the voice row module**

```css
.voice { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; gap: var(--sp-3); align-items: center; min-height: var(--density-row); padding: var(--density-pad) var(--sp-4); border-bottom: 1px solid var(--border-subtle); min-width: 0; }
.name { color: var(--text); font-size: var(--fs-md); min-width: 0; }
.locale { font-family: var(--font-mono); font-size: var(--fs-sm); color: var(--text-subtle); }
.unavailable { color: var(--text-subtle); font-size: var(--fs-sm); }
.actions { display: flex; gap: var(--sp-2); }
```

A voice with no model or licence shows guidance text and **no** play control — that is the existing A02 behaviour, not a styling choice.

- [ ] **Step 2: Write the player module**

```css
.player { display: flex; flex-direction: column; gap: var(--sp-2); padding: var(--sp-4); background: var(--surface); border: 1px solid var(--border-control); border-radius: var(--radius); min-width: 0; }
.audio { width: 100%; }
.scrub { width: 100%; accent-color: var(--primary); }
.row { display: flex; align-items: center; gap: var(--sp-3); flex-wrap: wrap; }
.hash { font-family: var(--font-mono); font-size: var(--fs-sm); color: var(--text-subtle); }
.warning { color: var(--warning); font-size: var(--fs-sm); }
```

`accent-color` colours the native scrubber. Keep the `<audio controls>` element pointing straight at the artifact URL — never a Blob of the whole master.

- [ ] **Step 3: Apply, preserving A02/A05 behaviour**

Keep: one shared player with `playing ≠ selected`; the char counter and client-side cap; the cache badge; cancel/retry; A/B compare on one normalized text; the `AUDIO_PLAYBACK_FAILED`/`AUDIO_SOURCE_UNAVAILABLE` warnings; the “new master loaded” warning on sha change; the approval block when the server master differs from the one auditioned.

- [ ] **Step 4: Run**

Run: `cd frontend && npx vitest run && npx playwright test e2e/single-voice.spec.ts e2e/audio-range.spec.ts`
Expected: PASS, including GET 200 + ETag, 206 on `Range: bytes=0-9`, 416 on an oversized range, 404 on a cross-chapter artifact, and the 409 on approving with a stale hash.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/screens/ frontend/src/features/voices/ frontend/src/features/audio/
git commit -m "feat(ui): restyle the voice browser, preview panel and artifact player"
```

---

## Task 22: Export workflow and the seven settings groups

**Files:**
- Create: modules for `ExportWorkflow`, `StorageSettings`, `ProfileEditor`, `modelCatalog`, `StyleManager`, `GlossaryManager`, `CharacterManager`, `MemoryManager`, `Diagnostics`
- Modify: those components
- Test: `frontend/e2e/export-review.spec.ts`, `frontend/e2e/provider-credentials.spec.ts`, `frontend/src/features/settings/*.test.tsx`

**Interfaces:**
- Consumes: `Table`, `Button`, `Input`, `Select`, `Tabs`, `Drawer`.
- Produces: export and settings on the token system.

- [ ] **Step 1: Write the export result module**

```css
.workflow { display: flex; flex-direction: column; gap: var(--sp-4); padding: var(--sp-4); min-width: 0; }
.gate { display: flex; align-items: center; gap: var(--sp-2); padding: var(--sp-3); border: 1px solid var(--border-control); border-left: 3px solid var(--text-muted); background: var(--surface); }
.gateReady { border-left-color: var(--success); }
.gateBlocked { border-left-color: var(--danger); }
.hash { font-family: var(--font-mono); font-size: var(--fs-sm); color: var(--text-subtle); }
.fileGroup { border-top: 1px solid var(--border-control); padding: var(--sp-3) 0; }
.fileKind { font-size: var(--fs-sm); font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: var(--text-muted); }
.fileRow { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: var(--sp-3); padding: var(--sp-2) 0; border-bottom: 1px solid var(--border-subtle); min-width: 0; }
.stale { color: var(--warning); font-size: var(--fs-sm); display: flex; align-items: center; gap: var(--sp-2); }
.notice { color: var(--text-subtle); font-size: var(--fs-sm); }
```

- [ ] **Step 2: Write the settings group module**

```css
.groups { display: grid; grid-template-columns: 240px minmax(0, 1fr); min-height: 0; height: 100%; }
.groupNav { border-right: 1px solid var(--border-subtle); padding: var(--sp-3) 0; overflow-y: auto; min-width: 0; }
.groupLink { display: block; padding: var(--sp-2) var(--sp-4); color: var(--text-muted); text-decoration: none; font-size: var(--fs-md); min-height: var(--density-row); }
.groupLink:hover { color: var(--text); background: var(--surface); }
.groupLink[aria-current='page'] { color: var(--text); box-shadow: inset 2px 0 0 0 var(--primary); }
.groupLink:focus-visible { outline: var(--focus-ring) solid var(--focus); outline-offset: calc(-1 * var(--focus-offset)); }
.groupBody { padding: var(--sp-5); overflow-y: auto; min-width: 0; }
@media (max-width: 1023px) {
  .groups { grid-template-columns: minmax(0, 1fr); }
  .groupNav { display: none; }
}
```

- [ ] **Step 3: Apply, preserving the security-relevant behaviour**

Keep, unchanged:
- `ExportWorkflow`: client-side metadata validation that blocks the call on an empty title or `volume < 1`; publication disabled when the gate blocks; the C08 “studio không tự upload” notice; `data-testid="export-workflow"` and `data-testid="manifest-export-private-1"`; `'Sẵn sàng xuất bản'`, `'Checksum khớp'`, `'Tạo archive riêng tư'`, `'Đã tạo archive riêng tư.'`.
- `ProfileEditor`: the credential field renders empty, `type="password"`, and **no input ever holds the secret**; the secret goes only to the provisioning endpoint body; `secretConfigured` drives the label.
- `GlossaryManager`: “Xem phạm vi” previews affected segments and invalidated runs **before** saving, and does not auto-save.
- `CharacterManager`: the evidence gate.
- `MemoryManager`: only `APPROVED` entries appear as context; candidate approval needs run + segment.
- `StorageSettings`: retention creates a plan only (`applied: false`, nothing deleted); restore requires retyping the exact target and only for a verified backup; cleanup executes with the exact `planId` + `snapshotHash`.
- `AppearanceSettings`: the sanitizing save path — `preferencesAreSafe` rejects any value matching the content/secret pattern, and rejected keys are never echoed into the DOM.
- **No credential, key or token anywhere in `localStorage`.**

- [ ] **Step 4: Sweep the reset's collateral damage on this task's surfaces**

`styles/reset.css` (Task 2) strips the user-agent chrome from **every** `<button>` in the
document — `button { background: none; border: none; padding: 0; }` — and from every raw
`<ul>`/`<ol>` — `list-style: none; padding: 0;`. That is deliberate as a base layer, but it
means any element that never picks up a class is now **unstyled and unbounded**, not merely
unstyled. Two consequences you must actively close on every file this task touches:

1. **Raw `<button>` elements.** Not every button goes through the `Button` primitive. Find
   every literal `<button` in the files you are restyling and give it a real class from your
   module: padding of at least `var(--sp-2)` vertically, a visible `:hover`, and a
   `:focus-visible` ring (`outline: var(--focus-ring) solid var(--focus); outline-offset: var(--focus-offset);`).
   Check every settings group in your Files list and any screen you modify — a raw button
   with no class renders as bare text with **zero padding**, which drops its hit target well
   under the 24×24px WCAG 2.5.8 minimum and leaves it with no focus indicator at all.
2. **Marker-less `<ul>`/`<ol>`.** Any list that relied on a bullet to separate items now has
   none. Where the items are still visually distinct without it (each already has a border,
   a row layout, or its own block), leave it. Where they are not, restore the separation
   through your own module (a `gap`, a border, or a row padding) rather than by putting
   `list-style` back.

This step is not hypothetical tidying: the reset made these elements worse than the
unstyled default, so a screen can regress here while every test stays green.

- [ ] **Step 5: Run**

Run: `cd frontend && npx vitest run && npx playwright test`
Expected: PASS across all 9 specs. `provider-credentials.spec.ts` must still confirm an empty credential field and no secret in `localStorage`/`sessionStorage` at three points.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/exports/ frontend/src/features/settings/ frontend/src/features/glossary/ frontend/src/features/characters/ frontend/src/features/translation/ frontend/src/routes/screens/
git commit -m "feat(ui): restyle the export workflow and the seven settings groups"
```

---

# G6 — Responsive and accessibility hardening

## Task 23: Reflow at 320/390/1024/1366 and the sub-1024px layout

**Files:**
- Modify: every `.module.css` with a fixed column
- Modify: `frontend/e2e/visual-a11y.spec.ts`
- Test: `frontend/e2e/visual-a11y.spec.ts`

**Interfaces:**
- Consumes: everything.
- Produces: no horizontal scrolling at any of the four widths.

- [ ] **Step 1: Write the failing assertions**

Extend `visual-a11y.spec.ts` with an explicit no-horizontal-overflow check on each main route:

```ts
for (const width of [320, 390, 1024, 1366]) {
  test(`no horizontal overflow at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    for (const route of ['/', '/jobs', '/settings/appearance']) {
      await page.goto(route);
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow, `${route} overflows by ${overflow}px at ${width}px`).toBeLessThanOrEqual(1);
    }
  });
}
```

- [ ] **Step 2: Run to find the real offenders**

Run: `cd frontend && npx playwright test e2e/visual-a11y.spec.ts`
Expected: likely FAIL on at least one route — this is the same class of bug found in `149c729`, where a grid `auto` column was pinned by a `<select>`'s max-content.

- [ ] **Step 3: Fix every offender**

The rule that fixes this class: any grid column that can hold a control or long content must be `minmax(0, 1fr)`, and the element inside it needs `min-width: 0`. `<select>`, `<input>`, and long unbroken strings are the usual culprits. Also add `overflow-wrap: anywhere` to `.sourcePath`, `.entry`, and `.hash` — file paths and hashes have no spaces.

Do not fix an overflow by adding `overflow-x: hidden` anywhere above the offending element. Hide the symptom and the next reflow bug is invisible.

- [ ] **Step 4: Verify all four widths**

Run: `cd frontend && npx playwright test e2e/visual-a11y.spec.ts`
Expected: PASS at 320, 390, 1024, 1366.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/**/*.module.css frontend/e2e/visual-a11y.spec.ts
git commit -m "fix(ui): remove horizontal overflow at 320, 390, 1024 and 1366"
```

---

## Task 24: Keyboard, zoom, CJK and reduced motion

**Files:**
- Modify: `frontend/e2e/visual-a11y.spec.ts`
- Modify: any component that fails
- Test: `frontend/e2e/visual-a11y.spec.ts`

**Interfaces:**
- Consumes: everything.
- Produces: the plan's acceptance evidence.

- [ ] **Step 1: Add the four remaining acceptance cases**

```ts
test('text-only zoom to 200% clips nothing at 1280 and 1024', async ({ page }) => {
  for (const width of [1280, 1024]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/');
    await page.evaluate(() => {
      document.documentElement.style.fontSize = '200%';
    });
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow, `overflow at ${width}px at 200% text`).toBeLessThanOrEqual(1);
  }
});

test('the primary flow is keyboard completable', async ({ page }) => {
  await page.goto('/');
  await page.keyboard.press('Tab');
  const first = await page.evaluate(() => document.activeElement?.tagName);
  expect(first).not.toBe('BODY');
  const outline = await page.evaluate(() => {
    const style = window.getComputedStyle(document.activeElement as Element);
    return { width: style.outlineWidth, style: style.outlineStyle };
  });
  expect(outline.style).not.toBe('none');
});

test('reduced motion removes transitions', async ({ browser }) => {
  const context = await browser.newContext({ reducedMotion: 'reduce' });
  const page = await context.newPage();
  await page.goto('/');
  const duration = await page.evaluate(() => {
    const target = document.querySelector('button');
    return target ? window.getComputedStyle(target).transitionDuration : '0s';
  });
  expect(['0s', '0.01ms', '0.00001s']).toContain(duration);
  await context.close();
});
```

Then add the prose-integrity check to `e2e/single-voice.spec.ts` — that test already seeds the CJK fixture `第一章\n林动说：“你好。”` (line 45) and reaches the translation route at line 53, so it is the one place where real CJK prose is on screen. Insert immediately after the `Dịch & Hiệu đính` heading assertion (currently line 54), before translation runs:

```ts
  // Uppercase is a no-op on CJK, so a rule that uppercases prose is invisible in
  // the rendered text and can only be caught by computed style. Prove the fixture
  // is actually rendered first, or the assertion below could pass on an empty set.
  const cjkElementCount = await page.evaluate(() => {
    let count = 0;
    document.querySelectorAll<HTMLElement>('body *').forEach((el) => {
      const ownText = Array.from(el.childNodes)
        .filter((node) => node.nodeType === Node.TEXT_NODE)
        .map((node) => node.textContent ?? '')
        .join('');
      if (/[㐀-鿿]/.test(ownText)) count += 1;
    });
    return count;
  });
  expect(cjkElementCount, 'the CJK fixture must be on screen before this check means anything').toBeGreaterThan(0);

  const uppercasedProse = await page.evaluate(() => {
    const bad: string[] = [];
    document.querySelectorAll<HTMLElement>('body *').forEach((el) => {
      const ownText = Array.from(el.childNodes)
        .filter((node) => node.nodeType === Node.TEXT_NODE)
        .map((node) => node.textContent ?? '')
        .join('');
      if (!/[㐀-鿿]/.test(ownText)) return;
      const transform = window.getComputedStyle(el).textTransform;
      if (transform !== 'none') bad.push(`${el.tagName}:"${ownText.trim().slice(0, 20)}" [${transform}]`);
    });
    return bad;
  });
  expect(uppercasedProse, `uppercased CJK prose: ${uppercasedProse.join(' | ')}`).toEqual([]);
```

- [ ] **Step 2: Run**

Run: `cd frontend && npx playwright test e2e/visual-a11y.spec.ts`
Expected: PASS. Any failure here is a real defect in the implementation, not a test to relax — in particular, a non-`none` `text-transform` on prose means the uppercase rule leaked out of interface labels.

- [ ] **Step 3: Run the full gate**

```bash
cd frontend && npx vitest run && npm run build && npx playwright test
cd .. && .venv/Scripts/python.exe -m pytest backend/tests -q
```

Expected: vitest ≥ 272 + new tests, all green. Build PASS. Playwright 9 specs + new cases green. Backend **1258 passed** — this work touches no backend file, so any backend failure is pre-existing or comes from a stray edit; investigate rather than rerunning.

**And the repo-wide colour check — this is its home; Task 16 deliberately does not run it.** Only here,
after G5 has converted every screen, is "one colour system" a claim that can be true:

```bash
cd frontend && grep -rnE "#[0-9a-fA-F]{6}" src/ --include=*.tsx --include=*.ts --include=*.css | grep -v "\.test\." | grep -v "styles/tokens.css"
```

Expected: no hits outside `tokens.css` (the contrast test reads hex from CSS, and test files may keep
expectations). Any surviving hit is a screen G5 missed — fix it, do not relax the check.

- [ ] **Step 4: Commit**

```bash
git add frontend/e2e/visual-a11y.spec.ts frontend/src
git commit -m "test(ui): gate keyboard, 200% zoom, CJK integrity and reduced motion"
```

---

## Self-Review Notes

**Spec coverage.** Every spec section maps to a task: §2.1–2.4 palette and the dark default → Tasks 1, 4; §2.2 border split → Task 1; §3.1 styles layer → Tasks 1–2; §3.2 ThemeProvider and boot script → Task 3; §3.3 primitives → Tasks 5–8; §3.4 router split and guards → Tasks 11–16; §4 G1–G6 → the six phase headings; §5 gates → Task 24 Step 3; §6 constraints → Global Constraints plus the per-task preservation lists; §7 risks → the boot-parity test (Task 3 Step 6), the uppercase rule enforced in Tasks 6, 18, 20 and asserted in Task 24, the `minmax(0,1fr)` rule in Conventions and Task 23, and the primitive/screen drift rule in Task 16 Step 3.

**One spec correction this plan forces.** The spec's risk table says the boot script and `ThemeProvider` "gọi cùng một hàm phân giải, không chép logic". They cannot — the boot script runs before the bundle and cannot import TypeScript. Task 3 resolves it differently: the boot script is the only place that resolves at startup, and `ThemeProvider` seeds from the DOM attribute rather than re-resolving, so there is still exactly one resolution per load. Step 6 adds a parity test that evaluates the real script body from `index.html` against `resolveTheme` for all six cases, so the two cannot drift silently. The spec's risk row should be updated to describe this.

**Type consistency.** `resolveTheme`, `applyPreferences`, `readStoredPreferences`, `subscribePreferences`, `notifyPreferencesChanged` and `UI_PREFERENCES_STORAGE_KEY` are defined in Task 3 and used with those exact names in Tasks 9 and 10. `styles.button` / `styles[variant]` in Task 5 match the class names in `Button.module.css`. Every token name used from Task 5 onward appears in the Conventions table and is declared in Task 1 or Task 2.
