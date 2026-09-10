import { expect, test } from '@playwright/test';

const origin = 'http://127.0.0.1:8765';

/**
 * U05 — browser E2E for the tab/dock workspace (the remaining U05 item that was
 * only covered by unit/reducer tests until now).
 *
 * Drives the real app on a real browser: in-app navigation opens tabs, the
 * WAI-ARIA keyboard model moves focus and activates, dock/undock renders the
 * secondary pane, a saved layout survives a full page reload, and closing the
 * active tab navigates to the surviving tab.
 */
test('workspace tabs: keyboard, dock, save and close on a real browser', async ({
  page,
  request,
}) => {
  const tokenResponse = await request.get('/api/security/bootstrap');
  const token = (await tokenResponse.json()) as { csrfToken: string };
  const headers = { Origin: origin, 'X-CSRF-Token': token.csrfToken };

  const projectResponse = await request.post('/api/projects', {
    headers,
    data: {
      title: 'Tab E2E',
      slug: `tab-e2e-${Date.now()}`,
      source_type: 'SELF_AUTHORED',
      rights_status: 'CLEARED',
    },
  });
  expect(projectResponse.ok()).toBeTruthy();
  const project = (await projectResponse.json()) as { id: string };

  await page.goto(`/projects/${project.id}/import`);

  const tablist = page.getByRole('tablist', { name: 'Tab đang mở' });
  await expect(tablist).toBeVisible();
  const importTab = page.getByRole('tab', { name: 'Nhập nội dung' });
  await expect(importTab).toBeVisible();
  await expect(importTab).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByLabel('Số tab')).toHaveText('1/8');

  // In-app navigation opens a second tab for the visited route.
  await page.getByRole('link', { name: 'Hàng đợi batch' }).click();
  await expect(page).toHaveURL(new RegExp(`/projects/${project.id}/batch`));
  const batchTab = page.getByRole('tab', { name: 'Hàng đợi batch' });
  await expect(batchTab).toBeVisible();
  await expect(batchTab).toHaveAttribute('aria-selected', 'true');
  await expect(importTab).toHaveAttribute('aria-selected', 'false');
  await expect(page.getByLabel('Số tab')).toHaveText('2/8');

  // Keyboard: ArrowRight/ArrowLeft move focus, Enter activates the focused tab.
  await importTab.focus();
  await page.keyboard.press('ArrowRight');
  await expect(batchTab).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(batchTab).toHaveAttribute('aria-selected', 'true');

  await page.keyboard.press('ArrowLeft');
  await expect(importTab).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(new RegExp(`/projects/${project.id}/import`));
  await expect(importTab).toHaveAttribute('aria-selected', 'true');

  // Home/End move focus to the first/last tab.
  await page.keyboard.press('End');
  await expect(batchTab).toBeFocused();
  await page.keyboard.press('Home');
  await expect(importTab).toBeFocused();

  // Dock the secondary pane, then undock it again.
  await page.getByRole('button', { name: 'Dock “Hàng đợi batch”' }).click();
  const dock = page.getByRole('region', { name: 'Khung phụ' });
  await expect(dock).toBeVisible();
  await expect(dock.getByText('Hàng đợi batch')).toBeVisible();
  await page.getByRole('button', { name: 'Bỏ dock' }).click();
  await expect(page.getByRole('region', { name: 'Khung phụ' })).toBeHidden();

  // Persist the layout, then verify it is restored after a full reload.
  await page.getByRole('button', { name: 'Lưu layout' }).click();
  await expect(page.getByRole('status')).toContainText('bản 1');

  await page.reload();
  await expect(page.getByRole('status')).toContainText('bản 1');
  await expect(page.getByRole('tab', { name: 'Nhập nội dung' })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Hàng đợi batch' })).toBeVisible();

  // Closing the active tab falls back to the surviving tab.
  await page.getByRole('button', { name: 'Đóng tab Nhập nội dung' }).click();
  await expect(page.getByRole('tab', { name: 'Nhập nội dung' })).toHaveCount(0);
  await expect(page).toHaveURL(new RegExp(`/projects/${project.id}/batch`));
  await expect(page.getByRole('tab', { name: 'Hàng đợi batch' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
});
