import { expect, test } from '@playwright/test';

const origin = 'http://127.0.0.1:8765';

test('fake single narrator reaches verified publication bundle', async ({
  page,
  request,
}) => {
  const tokenResponse = await request.get('/api/security/bootstrap');
  const token = (await tokenResponse.json()) as { csrfToken: string };
  const headers = { Origin: origin, 'X-CSRF-Token': token.csrfToken };

  const projectResponse = await request.post('/api/projects', {
    headers,
    data: {
      title: 'Kiếm hiệp mẫu',
      slug: `kiem-hiep-mau-e2e-${Date.now()}`,
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
      items: [
        { ordinal: 1, title: '第一章', text: '第一章\n林动说：“你好。”' },
      ],
    },
  });
  expect(importResponse.ok()).toBeTruthy();
  const imported = (await importResponse.json()) as { chapters: { id: string }[] };
  const chapterId = imported.chapters[0].id;

  await page.goto(`/chapters/${chapterId}/translation`);
  await expect(page.getByRole('heading', { name: 'Dịch & Hiệu đính' })).toBeVisible();

  // Fake (local, offline) translation then approval.
  await page.getByRole('button', { name: 'Dịch convert nội bộ' }).click();
  await expect(page.getByText('Đã dịch hoàn tất')).toBeVisible();
  await page.getByRole('button', { name: /Phê duyệt chuẩn/ }).click();

  // One voice render, then approve the master audio.
  await expect(page.getByRole('heading', { name: 'Giọng đọc' })).toBeVisible();
  await page.getByRole('button', { name: 'Render một giọng' }).click();
  await expect(page.getByRole('heading', { name: 'Audio' })).toBeVisible();
  await expect(page.getByText(/sẵn sàng duyệt/)).toBeVisible();
  await page.getByRole('button', { name: 'Phê duyệt audio' }).click();

  // Publication export is allowed after rights and audio approval (E01 review panel).
  await expect(page.getByTestId('export-workflow')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Tạo bundle publication' })).toBeEnabled();
  await page.getByLabel('Tiêu đề tập').fill('Tập 1');
  await page.getByRole('button', { name: 'Tạo bundle publication' }).click();
  await expect(page.getByText('Đã tạo bundle publication.')).toBeVisible();
  await expect(page.getByText(/studio không tự upload/)).toBeVisible();
});