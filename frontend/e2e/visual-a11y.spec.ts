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

test('text resized to 200% keeps content readable without clipping (WCAG 1.4.4)', async ({ page }) => {
  // WCAG 1.4.4 (Resize text) is evaluated at a normal desktop viewport: text
  // must survive 200% scaling without loss of content or horizontal scrolling.
  for (const width of [1280, 1024]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/');
    await page.addStyleTag({ content: 'html { font-size: 200% !important; }' });
    await page.waitForTimeout(150);

    await expect(page.getByRole('heading', { name: 'Truyện Audio Studio' })).toBeVisible();
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
    expect(clipped, `clipped text at 200% on ${width}px: ${clipped.join(', ')}`).toEqual([]);
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