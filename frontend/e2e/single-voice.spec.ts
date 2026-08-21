import { expect, test } from '@playwright/test';

test('fake single narrator reaches verified publication bundle', async ({ page }) => {
  await page.goto('/projects/new');
  await page.getByLabel('Tên truyện').fill('Kiếm hiệp mẫu');
  await page.getByLabel('Loại nguồn').selectOption('SELF_AUTHORED');
  await page.getByLabel('Trạng thái quyền').selectOption('CLEARED');
  await page.getByLabel('Bằng chứng quyền').setInputFiles('e2e/fixtures/author-permission.txt');
  for (const scope of ['TRANSLATE_VI', 'CREATE_AUDIO', 'PUBLIC_STREAM']) {
    await page.getByLabel(scope).check();
  }
  await page.getByRole('button', { name: 'Tạo dự án' }).click();
  await page.getByRole('link', { name: 'Nhập nội dung' }).click();
  await page.getByLabel('Văn bản Trung').fill('第一章\n林动说：“你好。”');
  await page.getByRole('button', { name: 'Xác nhận snapshot' }).click();
  await page.getByRole('button', { name: 'Dịch bằng fake' }).click();
  await expect(page.getByText('Chờ duyệt bản dịch')).toBeVisible();
  await page.getByRole('button', { name: 'Phê duyệt bản dịch' }).click();
  await page.getByRole('button', { name: 'Render một giọng' }).click();
  await page.getByRole('button', { name: 'Phê duyệt audio' }).click();
  await page.getByRole('button', { name: 'Tạo bundle publication' }).click();
  await expect(page.getByText('Đã verify checksum')).toBeVisible();
});
