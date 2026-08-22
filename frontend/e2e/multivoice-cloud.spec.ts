import { expect, test } from '@playwright/test';

test('cloud is blocked until consent and only changed role rerenders', async ({ page }) => {
  await page.goto('/multivoice-cloud-demo');
  await page.getByRole('button', { name: 'Đa giọng có hỗ trợ' }).click();
  await page.getByRole('button', { name: 'Thêm vai' }).click();
  await page.getByLabel('Tên vai').fill('Nữ chính');
  await expect(page.getByText('Chưa cấp đồng ý xử lý cloud')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Dùng Google Neural2' })).toBeDisabled();
  await page.getByRole('button', { name: 'Cấp fake consent và rate card' }).click();
  await page.getByRole('button', { name: 'Dùng Google Neural2' }).click();
  await page.getByLabel('Vai cho đoạn seg-2').selectOption('hero');
  await page.getByRole('button', { name: 'Lưu assignment' }).click();
  await expect(page.getByText('Tái sử dụng 2 đoạn; render lại 1 đoạn')).toBeVisible();
});
