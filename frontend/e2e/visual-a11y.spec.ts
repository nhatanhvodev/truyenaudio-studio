import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

// U01 / U05 / V02 browser-visual evidence on a real system browser (Playwright,
// channel 'chrome'). These are real-browser layout assertions, not DOM-only unit
// checks; the e2e server seeds the fake voice preset and serves the built app on
// 127.0.0.1:8765.

const VIEWPORTS = [320, 390, 1024, 1366] as const;

async function horizontalOverflow(page: Page): Promise<{ scrollWidth: number; clientWidth: number }> {
  return page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
}

async function expectNoHorizontalOverflow(page: Page) {
  const { scrollWidth, clientWidth } = await horizontalOverflow(page);
  expect(
    scrollWidth,
    `page must not scroll horizontally (scrollWidth=${scrollWidth} clientWidth=${clientWidth})`,
  ).toBeLessThanOrEqual(clientWidth + 1);
}

/**
 * Names of the elements whose computed transition-duration is still non-zero.
 *
 * Shared by both reduced-motion axes (the in-app preference and the OS media
 * query) so the two cannot drift apart; the collector itself is unchanged from
 * the in-app test that first needed it.
 */
async function stillTransitioning(page: Page): Promise<string[]> {
  return page.evaluate(() =>
    Array.from(document.querySelectorAll<HTMLElement>('*'))
      .map((el) => ({
        name: `${el.tagName.toLowerCase()}${el.className ? `.${String(el.className).split(' ')[0]}` : ''}`,
        // transitionDuration is a LIST when an element transitions more than one
        // property ('width 1s, color 1s' computes to '1s, 1s'). Compare every
        // entry, not the raw string, or a multi-property transition reports as an
        // offender even though the reduce-motion rule neutralised all of it.
        durations: window.getComputedStyle(el).transitionDuration.split(',').map((part) => part.trim()),
      }))
      // getComputedStyle normalises transition-duration to seconds, so the
      // reduce-motion rule's 0.01ms arrives here as '1e-05s' (0.00001s). Any
      // duration at or below that is neutralised; '0s' covers elements that
      // never transitioned at all. Compare NUMERICALLY, never against a list of
      // spellings - an earlier draft allow-listed ['0s', '0.01ms', '0.00001s']
      // and missed '1e-05s', the form Chrome actually emits.
      .filter((entry) => entry.durations.some((d) => Number.parseFloat(d) > 0.00001))
      .map((entry) => `${entry.name} [${entry.durations.join(', ')}]`),
  );
}

/**
 * Names of the elements whose computed animation-duration is still non-zero.
 *
 * The animation half of the same declaration blocks: base.css neutralises
 * `animation-duration`/`animation-iteration-count` beside `transition-duration`,
 * and `stillTransitioning` cannot see any of it. That half matters more, not
 * less — the four animations the app declares (.pulseDot, .spinIcon and
 * .shimmerOverlay in features/import, .crawlBarIndeterminate in features/jobs)
 * are all `infinite`, which is exactly what WCAG 2.2.2 (Pause, Stop, Hide) and
 * 2.3.3 (Animation from Interactions) are about. Identical normalisation and the
 * same numeric comparison as the transition helper: the rule's 0.01ms reaches
 * getComputedStyle as '1e-05s'.
 *
 * `animation-iteration-count` is deliberately NOT collected here. It is a bare
 * number (Chrome reports the rule's `1` as '1' and an element with no animation
 * as '1' too), so the property cannot distinguish "neutralised" from "never
 * animated" at all. The animation-duration guard below is what carries the
 * proof that the rule ran; see its comment.
 */
async function stillAnimating(page: Page): Promise<string[]> {
  return page.evaluate(() =>
    Array.from(document.querySelectorAll<HTMLElement>('*'))
      .map((el) => ({
        name: `${el.tagName.toLowerCase()}${el.className ? `.${String(el.className).split(' ')[0]}` : ''}`,
        durations: window.getComputedStyle(el).animationDuration.split(',').map((part) => part.trim()),
      }))
      .filter((entry) => entry.durations.some((d) => Number.parseFloat(d) > 0.00001))
      .map((entry) => `${entry.name} [${entry.durations.join(', ')}]`),
  );
}

/**
 * Non-vacuity guard for one property of one reduce-motion rule block.
 *
 * Names the elements still computing the untouched initial `0s`, i.e. the ones
 * the block never reached. This exists because BOTH offender collectors above
 * return `[]` on the routes these tests visit whether or not the rule exists:
 * the app's two transitions and its four animations are progress/crawl
 * indicators, and none of them renders on `/` or `/settings/appearance`. An
 * `expect(offenders).toEqual([])` alone would therefore pass with the entire
 * declaration block deleted — which is how this file has already produced one
 * vacuous assertion.
 *
 * What the rule does leave behind is proof that it ran. It sets the property on
 * EVERY element it matches, so under reduced motion no matched element may
 * still compute the player's initial `0s`. Chrome reports the rule's 0.01ms as
 * '1e-05s', and parseFloat reads that as 0.00001 — the comparison below is
 * `=== 0`, so an element that parses to exactly zero is one the rule did not
 * reach. Revert the block and this list fills up.
 *
 * Callers must compare the result against `elementsNotMatchedBy` rather than
 * `[]`: `0s` is also the honest computed value of every element outside the
 * block's subject selector, so an empty-list assertion would be wrong wherever
 * the subject is narrower than `*`, and an allow-list of tag names would hide a
 * real leak the day one of those tags gains a transition.
 */
async function stillAtInitialDuration(
  page: Page,
  property: 'transitionDuration' | 'animationDuration',
): Promise<string[]> {
  return page.evaluate((prop) => {
    return Array.from(document.querySelectorAll<HTMLElement>('*'))
      .filter((el) =>
        window
          .getComputedStyle(el)
          [prop].split(',')
          .every((part) => Number.parseFloat(part) === 0),
      )
      .map((el) => el.tagName.toLowerCase());
  }, property);
}

/**
 * The elements a rule block's subject selector cannot match at all — the
 * complement of what that block can possibly reach.
 *
 * This deliberately asks the SELECTOR, not the stylesheet, so the answer does
 * not change when the rule block is deleted. Put beside
 * `stillAtInitialDuration`, that is what gives the pair its teeth: remove the
 * block and the first set becomes "every element" while this one stays put, so
 * the equality between them fails loudly. Subtract this set before asserting
 * (rather than comparing against it) and the guard would go quietly vacuous
 * again — so the comparison is the assertion.
 */
async function elementsNotMatchedBy(page: Page, subject: string): Promise<string[]> {
  return page.evaluate(
    (selector) =>
      Array.from(document.querySelectorAll<HTMLElement>('*'))
        .filter((el) => !el.matches(selector))
        .map((el) => el.tagName.toLowerCase()),
    subject,
  );
}

test('project wizard reflows without horizontal overflow at 320/390/1024/1366', async ({ page }) => {
  for (const width of VIEWPORTS) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Truyện Audio Studio' })).toBeVisible();
    await expectNoHorizontalOverflow(page);
    // The create form is part of the same screen and must also fit. It opens by
    // default; the toggle collapses it, so collapse+expand covers both states.
    await page.getByRole('button', { name: '✕ Thu gọn form tạo' }).click();
    await expectNoHorizontalOverflow(page);
    await page.getByRole('button', { name: '+ Tạo dự án mới' }).click();
    await expect(page.getByRole('heading', { name: 'Tạo dự án mới' })).toBeVisible();
    await expectNoHorizontalOverflow(page);
  }
});

// Routes a user can reach with no project open. `/` is kept for uniformity: it
// is the route most likely to regress. The workspace tab strip lives in the
// shell, so every route renders it.
//
// `/jobs` is data-dependent: its job rows only exist once another spec in the
// run has driven a job to completion, so the populated case (a raw job UUID in
// a table cell) is exercised by the full-suite run, not by this file alone.
const ROUTES = ['/', '/jobs', '/settings/appearance'] as const;

for (const width of VIEWPORTS) {
  test(`app routes reflow without horizontal overflow at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    for (const route of ROUTES) {
      await page.goto(route);
      await expectNoHorizontalOverflow(page);
    }
  });
}

test('workspace tab labels stay readable instead of being ellipsised at 320px', async ({ page }) => {
  // Every visited route opens a tab, so in-app navigation is what fills the
  // strip — a single `page.goto` never produces more than one. The tab strip is
  // wider than 320px once three tabs are open, so this is where `.tab`'s
  // `text-overflow: ellipsis` used to truncate every label to a few characters.
  await page.setViewportSize({ width: 320, height: 900 });
  await page.goto('/projects/p1/import');
  await page.getByRole('link', { name: 'Dự án', exact: true }).click();
  await page.getByRole('link', { name: 'Jobs', exact: true }).click();
  await page.getByRole('link', { name: 'Diagnostics', exact: true }).click();
  await expect(page.getByRole('tablist', { name: 'Tab đang mở' })).toBeVisible();

  const truncated = await page.evaluate(() =>
    Array.from(document.querySelectorAll<HTMLElement>('[role="tab"]'))
      .filter((tab) => tab.clientWidth > 0 && tab.scrollWidth > tab.clientWidth + 1)
      .map((tab) => `${(tab.textContent ?? '').trim()} (${tab.clientWidth}<${tab.scrollWidth})`),
  );
  expect(truncated, `tab labels truncated at 320px: ${truncated.join(', ')}`).toEqual([]);

  // The strip scrolls; the document must still not.
  await expectNoHorizontalOverflow(page);
});

test('the settings body keeps a usable width beside the group nav at 320px', async ({ page }) => {
  // A fixed-width side nav can starve the settings body even when the page
  // reflows: `.main` carries `min-width: 0`, so it is allowed to shrink, and
  // whatever survives is all the group panels get. Reverting the `.nav`/`.main`
  // flex fix reproduces BOTH symptoms at once (document scrollWidth 354 against
  // clientWidth 320, and this body crushed to 16px), so the overflow assertions
  // above do see it. This test is worth keeping because it names the usable
  // width directly, and would still fail if a future `overflow-x: hidden` masked
  // the document overflow while leaving the body just as unusable.
  await page.setViewportSize({ width: 320, height: 900 });
  await page.goto('/settings/appearance');
  const body = page.locator('section[aria-label="Appearance"]');
  await expect(body).toBeVisible();
  const box = await body.boundingBox();
  expect(
    box?.width ?? 0,
    `settings body must stay usable at 320px (measured ${box?.width}px)`,
  ).toBeGreaterThanOrEqual(240);
});

test('text resized to 200% keeps content readable without clipping (WCAG 1.4.4)', async ({ page }) => {
  // WCAG 1.4.4 (Resize text) is evaluated at a normal desktop viewport: text
  // must survive 200% scaling without loss of content or horizontal scrolling.
  //
  // Two routes, because `/` alone never reaches a project screen. The second one
  // renders a workspace tab whose label is long enough for `.tab`'s
  // `max-width`/ellipsis to matter.
  const routes = ['/', '/projects/p1/import'] as const;
  for (const width of [1280, 1024]) {
    await page.setViewportSize({ width, height: 900 });
    for (const route of routes) {
      await page.goto(route);
      await page.addStyleTag({ content: 'html { font-size: 200% !important; }' });
      await page.waitForTimeout(150);

      if (route === '/') {
        await expect(page.getByRole('heading', { name: 'Truyện Audio Studio' })).toBeVisible();
      }
      await expectNoHorizontalOverflow(page);

      // No visible text container may clip its own content at 200% text size.
      const clipped = await page.evaluate(() => {
        const offenders: string[] = [];
        document.querySelectorAll<HTMLElement>('p, h1, h2, h3, span, button, label, li').forEach((el) => {
          if (el.clientWidth === 0 || el.clientHeight === 0) return;
          if (el.scrollWidth > el.clientWidth + 1) {
            offenders.push(`${el.tagName}:"${(el.textContent ?? '').trim().slice(0, 30)}"`);
          }
        });
        return offenders.slice(0, 5);
      });
      expect(clipped, `clipped text at 200% on ${width}px ${route}: ${clipped.join(', ')}`).toEqual([]);
    }
  }
});

test('nested project settings groups render real content and keep keyboard focus', async ({ page }) => {
  await page.goto('/projects/p1/settings/tts');
  await expect(page.locator('main')).toBeVisible();

  // The first focusable element is the global nav's first link; tabbing must
  // reach it. Scoped to the global nav because the breadcrumb also has a
  // "Thư viện" link.
  const globalNav = page.getByRole('navigation', { name: 'Khu vực' });
  await page.keyboard.press('Tab');
  await expect(globalNav.getByRole('link', { name: 'Thư viện' })).toBeFocused();

  // Switching group renders the real Appearance panel (U10 content on the U02 tree).
  await page.getByRole('navigation', { name: 'Nhóm cài đặt dự án' })
    .getByRole('link', { name: 'Appearance' })
    .click();
  await expect(page.getByLabel('Theme')).toBeVisible();
  await expect(page.getByLabel('Cỡ chữ')).toBeVisible();
});

test('Vietnamese diacritics render unbroken and inputs take real keyboard focus', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 });
  // The wizard opens the create form by default, so no toggle click is needed.
  await page.goto('/projects/new');

  const heading = (await page.getByRole('heading', { name: 'Tạo dự án mới' }).textContent()) ?? '';
  expect(heading).not.toContain('\uFFFD');
  expect(heading).toContain('Tạo');

  const input = page.getByLabel('Tên truyện');
  await input.focus();
  await expect(input).toBeFocused();
  await input.fill('Kiếm hiệp mẫu — 林动');
  await expect(input).toHaveValue('Kiếm hiệp mẫu — 林动');
});

test('appearance preferences reach the document', async ({ page }) => {
  await page.goto('/');
  await page.evaluate(() => window.localStorage.clear());
  await page.reload();

  // Dark is the default, regardless of the host OS preference.
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');

  await page.goto('/settings/appearance');
  await page.getByRole('combobox', { name: 'Theme' }).selectOption('light');
  await page.getByRole('button', { name: 'Lưu tùy chọn hiển thị' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');

  await page.getByRole('combobox', { name: 'Cỡ chữ' }).selectOption('large');
  await page.getByRole('button', { name: 'Lưu tùy chọn hiển thị' }).click();
  // fontScale reaches the DOM as the ROOT FONT SIZE, not as density. An earlier
  // draft asserted data-density here, which was decorative: it is 'comfortable'
  // or 'compact' no matter what the font select did, so the assertion passed
  // even if the save wrote nothing. 112.5% is ROOT_FONT_SIZE.large
  // (themeRuntime.ts) - assert the value applyPreferences actually writes.
  await expect(page.locator('html')).toHaveAttribute('style', /font-size:\s*112\.5%/);
});

test('system theme follows the OS and is overridden by an explicit choice', async ({ browser }) => {
  const context = await browser.newContext({ colorScheme: 'light' });
  const page = await context.newPage();
  await page.goto('/settings/appearance');
  await page.getByRole('combobox', { name: 'Theme' }).selectOption('system');
  await page.getByRole('button', { name: 'Lưu tùy chọn hiển thị' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');

  await page.goto('/settings/appearance');
  await page.getByRole('combobox', { name: 'Theme' }).selectOption('dark');
  await page.getByRole('button', { name: 'Lưu tùy chọn hiển thị' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await context.close();
});

test('the in-app reduced-motion preference stops transitions on its own', async ({ browser }) => {
  // Explicitly no OS-level reduced motion: the only thing that can stop the
  // transitions below is the preference written to data-reduce-motion.
  const context = await browser.newContext({ reducedMotion: 'no-preference' });
  const page = await context.newPage();

  await page.goto('/settings/appearance');
  await page.getByRole('checkbox', { name: 'Giảm chuyển động' }).check();
  await page.getByRole('button', { name: 'Lưu tùy chọn hiển thị' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-reduce-motion', 'true');

  // This is where the test has real teeth, and it is the animation half of the
  // rule rather than the transition half. The block's subject selector sets
  // animation-duration on everything it matches, so no matched element may still
  // compute the initial 0s; the comparison against `elementsNotMatchedBy` is
  // what proves the block ran rather than the route being animation-free.
  //
  // The subject is a DESCENDANT selector, `:root[data-reduce-motion='true'] *`,
  // so it does not match the root element itself — the media-query block, written
  // `*, *::before, *::after`, does. `html` is therefore genuinely still at the
  // initial 0s under the in-app preference, and the expected set is derived from
  // the selector rather than allow-listed as ['html'] so that a narrower block
  // (or a deleted one) still turns this red. Reported as a latent asymmetry with
  // the media-query block, not fixed here: `scroll-behavior`, `animation` and
  // `transition` are declared on `html` nowhere in the app, so nothing is
  // currently left running — the difference only bites if a root-level
  // declaration is ever added.
  const inAppSubject = ":root[data-reduce-motion='true'] *";
  const inAppUnreachable = await elementsNotMatchedBy(page, inAppSubject);
  expect(
    inAppUnreachable,
    `the in-app block's subject cannot match: ${inAppUnreachable.join(', ')}`,
  ).toEqual(['html']);

  const untouchedAnimations = await stillAtInitialDuration(page, 'animationDuration');
  expect(
    untouchedAnimations,
    `elements the in-app preference never reached (animation-duration still 0s): ${untouchedAnimations.slice(0, 8).join(', ')}`,
  ).toEqual(inAppUnreachable);

  // The same guard for transitions: it is what makes the transition collector
  // below mean anything on a route with no transitions. It also covers `html`,
  // which stillTransitioning would report if the root ever gained a transition.
  const untouchedTransitions = await stillAtInitialDuration(page, 'transitionDuration');
  expect(
    untouchedTransitions,
    `elements the in-app preference never reached (transition-duration still 0s): ${untouchedTransitions.slice(0, 8).join(', ')}`,
  ).toEqual(inAppUnreachable);

  // Nothing may still be animating. The app's four animations are all `infinite`
  // crawl/job indicators, so they are absent from this route and this list is
  // empty either way — the guards above, not this line, are the evidence.
  const animating = await stillAnimating(page);
  expect(animating, `still animating: ${animating.slice(0, 5).join(' | ')}`).toEqual([]);

  // And every transition must be neutralised. Collect the offenders rather than
  // counting them, so a failure names the element — but note what this line can
  // and cannot catch: the app declares exactly two transitions
  // (features/import's `.progressFill` and shared/ui's `.fill`, both progress-bar
  // fills) and neither renders on /settings/appearance, so it returns [] whether
  // or not the rule works. It is an empty-but-honest check: it catches a
  // transition introduced on THIS route, and proves nothing about the two that
  // exist elsewhere. Do not read a pass here as "the transitions are
  // neutralised" — that claim belongs to the guards above, which go red when the
  // block is deleted, and to the OS-axis test.
  const offenders = await stillTransitioning(page);
  expect(offenders, `still transitioning: ${offenders.slice(0, 5).join(' | ')}`).toEqual([]);

  // And it must survive a reload, i.e. the boot script writes it too — not just
  // the React effect that ran after the first mount.
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-reduce-motion', 'true');

  await context.close();
});

test('the primary flow is keyboard completable and every stop shows focus', async ({ page }) => {
  // WCAG 2.4.7 (Focus visible), walked over the real tab sequence rather than
  // one stop: the first stop alone says nothing about the other nine, and the
  // focus ring is drawn per-selector, so a single component can lose it while
  // the first one keeps it.
  await page.goto('/');
  const stops: string[] = [];
  const bad: string[] = [];
  for (let i = 0; i < 10; i += 1) {
    await page.keyboard.press('Tab');
    const info = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null;
      if (!el) return null;
      const s = window.getComputedStyle(el);
      return {
        tag: el.tagName,
        label: (el.getAttribute('aria-label') ?? el.textContent ?? '').trim().slice(0, 24),
        outlineStyle: s.outlineStyle,
        outlineWidth: s.outlineWidth,
        boxShadow: s.boxShadow,
      };
    });
    if (!info) break;
    // A stop on BODY means the tab sequence ran off the end of the document.
    if (info.tag === 'BODY') break;
    stops.push(`${i + 1}:${info.tag}(${info.label})`);
    // WCAG 2.4.7: a visible focus indicator. Accept EITHER an outline or a
    // box-shadow ring - some designs draw the ring with box-shadow, and
    // treating outline-style:none as failure would misreport those.
    const hasOutline = info.outlineStyle !== 'none' && Number.parseFloat(info.outlineWidth) > 0;
    const hasShadow = info.boxShadow !== 'none' && info.boxShadow !== '';
    if (!hasOutline && !hasShadow) bad.push(`${i + 1}:${info.tag}(${info.label})`);
  }
  // Guard against a vacuous pass: if the loop collected nothing, the assertions
  // below would succeed on an empty set and prove nothing.
  expect(stops.length, 'the tab sequence must reach real focusable elements').toBeGreaterThanOrEqual(5);
  expect(bad, `focus stops with NO visible indicator: ${bad.join(', ')}`).toEqual([]);
});

test('the OS reduced-motion media query neutralises transitions document-wide', async ({ browser }) => {
  // The OTHER reduced-motion axis: `prefers-reduced-motion` coming from the
  // operating system, which styles/base.css implements as a
  // `*, *::before, *::after` rule. The in-app preference test above deliberately
  // disables this signal, so this axis is otherwise untested.
  const context = await browser.newContext({ reducedMotion: 'reduce' });
  const page = await context.newPage();
  await page.goto('/');

  // The emulated media query must genuinely be active, or the checks below
  // would hold for the wrong reason.
  expect(
    await page.evaluate(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches),
    'the emulated OS reduce-motion preference must be reported by matchMedia',
  ).toBe(true);

  // Non-vacuity guards, one per property the media block sets. The app declares
  // exactly two transitions and four animations (all crawl/job progress
  // indicators) and none of them is on this route, so the offender collectors
  // below would return an empty list even with the whole media block deleted.
  // What the `*` rule does leave behind is proof that it ran: it sets each
  // property on EVERY matched element, so under `reduce` no element may still
  // compute the untouched initial `0s`. Both of these go red on a revert.
  //
  // This block's subject is `*`, which matches the root element too, so unlike
  // the in-app block there is nothing it cannot reach and the expected set is
  // empty. `elementsNotMatchedBy` still supplies it, so the two axes stay one
  // shape and the `*` is stated rather than implied.
  const mediaUnreachable = await elementsNotMatchedBy(page, '*');
  expect(
    mediaUnreachable,
    `the media block's subject cannot match: ${mediaUnreachable.join(', ')}`,
  ).toEqual([]);

  const untouchedTransitions = await stillAtInitialDuration(page, 'transitionDuration');
  expect(
    untouchedTransitions,
    `elements the media query never reached (transition-duration still 0s): ${untouchedTransitions.slice(0, 8).join(', ')}`,
  ).toEqual(mediaUnreachable);
  const untouchedAnimations = await stillAtInitialDuration(page, 'animationDuration');
  expect(
    untouchedAnimations,
    `elements the media query never reached (animation-duration still 0s): ${untouchedAnimations.slice(0, 8).join(', ')}`,
  ).toEqual(mediaUnreachable);

  // And nothing may still be transitioning. Collect the offenders rather than
  // counting them, so a failure names the rule that leaked. Empty on this route
  // either way — see the guards above for what actually carries the proof.
  const offenders = await stillTransitioning(page);
  expect(
    offenders,
    `still transitioning under OS reduce-motion: ${offenders.slice(0, 5).join(' | ')}`,
  ).toEqual([]);

  // Same for animations, which is the half the transition collector never
  // reached: an `infinite` indicator left running here would be a WCAG 2.2.2
  // failure that `stillTransitioning` reports as clean.
  const animating = await stillAnimating(page);
  expect(
    animating,
    `still animating under OS reduce-motion: ${animating.slice(0, 5).join(' | ')}`,
  ).toEqual([]);

  await context.close();
});

/* -------------------------------------------------------------------------
 * Reachability of controls behind a `display: none` ancestor.
 *
 * The redesign added the app's first stylesheet — at the merge base (9ef4fee)
 * `git ls-tree -r 9ef4fee frontend/src` matched zero `.css` files and the whole
 * presentation layer was inline `style={{...}}`, so nothing could be hidden at
 * any viewport. CSS made hiding possible, and two rules used it to delete a
 * whole rail below 1024px, taking a `<select>` that was the only control
 * setting a real piece of state with it. `display: none` is not "off-screen":
 * it removes the element from the tab order, the accessibility tree and
 * hit-testing, so the state it drives is permanently dead.
 *
 * The class was structurally invisible to this file before: ROUTES above is
 * ['/', '/jobs', '/settings/appearance'] — neither affected route is ever
 * visited — and the media queries are `max-width: 1023px`, which never applies
 * at the 1024px breakpoint the overflow tests use.
 * ---------------------------------------------------------------------- */

/** Everything a user can operate. If one of these is in the DOM it must be usable. */
const CONTROL_SELECTOR = 'select, input, textarea, button, a[href]';

/**
 * Controls that are in the DOM but hidden from every user by a `display: none`
 * ancestor.
 *
 * Ancestors are walked with `getComputedStyle(...).display`, deliberately NOT
 * `offsetParent`: `offsetParent` is also null for `position: fixed` elements,
 * so it reports a visible sticky/fixed control as hidden, and it is unreliable
 * outside a real layout engine.
 */
async function controlsHiddenByDisplayNone(page: Page): Promise<string[]> {
  return page.evaluate((selector) => {
    const offenders: string[] = [];
    document.querySelectorAll<HTMLElement>(selector).forEach((el) => {
      let node: HTMLElement | null = el;
      while (node) {
        if (window.getComputedStyle(node).display === 'none') {
          const label = (el.getAttribute('aria-label') ?? el.textContent ?? '').trim().slice(0, 30);
          const where = `${node.tagName.toLowerCase()}${node.className ? `.${String(node.className).split(' ')[0]}` : ''}`;
          offenders.push(`${el.tagName}("${label}") inside ${where}`);
          return;
        }
        node = node.parentElement;
      }
    });
    return offenders;
  }, CONTROL_SELECTOR);
}

/** How many controls the sweep above actually examined (the non-vacuity guard). */
async function countControls(page: Page): Promise<number> {
  return page.evaluate((selector) => document.querySelectorAll(selector).length, CONTROL_SELECTOR);
}

/**
 * A chapter with a completed (fake, offline) translation run.
 *
 * The e2e server seeds only the fake voice preset, so `/chapters/<id>/translation`
 * is unreachable without creating a project, granting rights and importing a
 * chapter first — the same setup `e2e/single-voice.spec.ts` uses. The route
 * renders an empty shell until a run exists (`current_translation` 404s with
 * TRANSLATION_RUN_NOT_FOUND), so the seeding includes the run.
 */
async function seedTranslatedChapter(request: APIRequestContext): Promise<string> {
  const bootstrap = await request.get('/api/security/bootstrap');
  const { csrfToken } = (await bootstrap.json()) as { csrfToken: string };
  const headers = { Origin: 'http://127.0.0.1:8765', 'X-CSRF-Token': csrfToken };

  const projectResponse = await request.post('/api/projects', {
    headers,
    data: {
      title: 'Kiếm hiệp mẫu',
      slug: `kiem-hiep-mau-a11y-${Date.now()}`,
      source_type: 'SELF_AUTHORED',
      rights_status: 'CLEARED',
    },
  });
  expect(projectResponse.ok()).toBeTruthy();
  const project = (await projectResponse.json()) as { id: string };

  for (const scope of ['TRANSLATE_VI', 'CREATE_AUDIO', 'PUBLIC_STREAM']) {
    const grantResponse = await request.post(`/api/projects/${project.id}/rights/grants`, {
      headers,
      data: {
        scope,
        territory: 'VN',
        allows_ai_processing: true,
        allows_third_party_cloud: false,
        valid_from: new Date(Date.now() - 60_000).toISOString(),
        evidence_id: null,
      },
    });
    expect(grantResponse.ok()).toBeTruthy();
  }

  const importResponse = await request.post(`/api/projects/${project.id}/chapters/import`, {
    headers,
    data: {
      kind: 'PASTE',
      items: [{ ordinal: 1, title: '第一章', text: '第一章\n林动说：“你好。”' }],
    },
  });
  expect(importResponse.ok()).toBeTruthy();
  const imported = (await importResponse.json()) as { chapters: { id: string }[] };
  return imported.chapters[0].id;
}

test('the project settings group nav stays reachable at 390px', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 });
  await page.goto('/projects/p1/settings/tts');
  await expect(page.locator('main')).toBeVisible();

  // The invariant first, before the route-specific assertions below: it is the
  // one that generalises, and putting it ahead of them keeps its teeth
  // demonstrable (revert the `display: none` rule and this is what fails).
  //
  // Guard against a vacuous pass: a page that failed to render would satisfy the
  // sweep below and prove nothing. Measured 24 controls here (five group links,
  // the global nav, the breadcrumb and the TTS panel's own fields), so 5 is the
  // same floor the tab-sequence test uses and sits well clear of the real count.
  const examined = await countControls(page);
  expect(examined, 'the sweep must examine real controls at 390px').toBeGreaterThanOrEqual(5);
  expect(
    await controlsHiddenByDisplayNone(page),
    'no control in the DOM may sit inside a display: none subtree',
  ).toEqual([]);

  // The five group links are the only route into the settings groups and the
  // breadcrumb is the only way back up. `ProjectNav` reaches `translation` and
  // nothing else, so deleting this nav strands four of the five groups. The base
  // layout rendered it unconditionally.
  const groupNav = page.getByRole('navigation', { name: 'Nhóm cài đặt dự án' });
  for (const label of ['Translation', 'TTS', 'Storage', 'Appearance', 'Advanced']) {
    await expect(groupNav.getByRole('link', { name: label })).toBeVisible();
  }
});

test('the translation workspace keeps its rails and QA filter reachable at 390px', async ({ page, request }) => {
  await page.setViewportSize({ width: 390, height: 900 });
  const chapterId = await seedTranslatedChapter(request);
  await page.goto(`/chapters/${chapterId}/translation`);

  // Produce a run so the navigator renders at all, then wait for the state the
  // navigator's controls depend on.
  await page.getByRole('button', { name: 'Dịch convert nội bộ' }).click();
  await expect(page.getByText('Đã dịch hoàn tất')).toBeVisible();

  // The invariant first, before the control-specific assertions below: it is the
  // one that generalises, and putting it ahead of them keeps its teeth
  // demonstrable (revert the `display: none` rule and this is what fails).
  //
  // Guard against a vacuous pass, then the sweep over every control. Measured 27
  // controls here (the QA filter, the two segment textareas, the translate and
  // approve buttons, the Gemini links, plus the global nav).
  const examined = await countControls(page);
  expect(examined, 'the sweep must examine real controls at 390px').toBeGreaterThanOrEqual(5);
  expect(
    await controlsHiddenByDisplayNone(page),
    'no control in the DOM may sit inside a display: none subtree',
  ).toEqual([]);

  // The QA filter is the ONLY control that sets `filter`, and it lives in the
  // navigator rail. It must be present, laid out and keyboard reachable — not
  // merely in the DOM.
  const filter = page.locator('section[aria-label="Dịch và duyệt"] select');
  await expect(filter).toBeVisible();
  const box = await filter.boundingBox();
  expect(box?.width ?? 0, `QA filter must have a real width (measured ${box?.width}px)`).toBeGreaterThan(0);
  expect(box?.height ?? 0, `QA filter must have a real height (measured ${box?.height}px)`).toBeGreaterThan(0);
  await filter.focus();
  await expect(filter).toBeFocused();
});