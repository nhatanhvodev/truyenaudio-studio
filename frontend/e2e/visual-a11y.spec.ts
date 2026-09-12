import { expect, test, type Page } from '@playwright/test';

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
  // A fixed-width side nav plus `flex: 1` on the body does NOT overflow the
  // document (the body carries `min-width: 0`), so the overflow assertions above
  // cannot see it: the body is silently crushed to a ~48px column instead. This
  // measures the body itself.
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

  // Every transition must be neutralised. Collect the offenders rather than
  // counting them, so a failure names the rule that leaked.
  const offenders = await page.evaluate(() =>
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
      // never transitioned at all.
      .filter((entry) => entry.durations.some((d) => Number.parseFloat(d) > 0.00001))
      .map((entry) => `${entry.name} [${entry.durations.join(', ')}]`),
  );
  expect(offenders, `still transitioning: ${offenders.slice(0, 5).join(' | ')}`).toEqual([]);

  // And it must survive a reload, i.e. the boot script writes it too — not just
  // the React effect that ran after the first mount.
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-reduce-motion', 'true');

  await context.close();
});