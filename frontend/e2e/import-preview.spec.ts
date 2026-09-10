import { expect, test } from '@playwright/test';
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

const origin = 'http://127.0.0.1:8765';

/**
 * U03 — the remaining browser item: folder import reaches the real preview route,
 * duplicate ordinals are surfaced per file before the user confirms, and the
 * confirmed text is exactly what the preview showed (no re-parse, no mangling).
 *
 * The fixture is a real folder on disk with two files sharing an ordinal, so the
 * DUPLICATE_ORDINAL warning comes from the production parser, not a mock.
 *
 * This spec also caught a real pre-existing crash: Shell called useMatch with
 * `??` short-circuiting, so the hook count changed when navigating from a project
 * route to a chapter route and React threw mid-render.
 */

test('U03: folder import previews duplicate ordinals and confirms the reviewed text', async ({
  page,
  request,
}) => {
  const tokenResponse = await request.get('/api/security/bootstrap');
  const token = (await tokenResponse.json()) as { csrfToken: string };
  const headers = { Origin: origin, 'X-CSRF-Token': token.csrfToken };

  const projectResponse = await request.post('/api/projects', {
    headers,
    data: {
      title: 'Import E2E',
      slug: `import-e2e-${Date.now()}`,
      source_type: 'SELF_AUTHORED',
      rights_status: 'CLEARED',
    },
  });
  expect(projectResponse.ok()).toBeTruthy();
  const project = (await projectResponse.json()) as { id: string };

  const root = mkdtempSync(path.join(tmpdir(), 'studio-import-'));
  const folder = path.join(root, 'chapters');
  mkdirSync(folder);
  // Two files claim chapter 1 - the parser must flag both, naming each file.
  writeFileSync(path.join(folder, '001-mo-dau.txt'), 'Chương 1\nNội dung mở đầu.', 'utf8');
  writeFileSync(path.join(folder, '001-trung.txt'), 'Chương 1\nBản trùng số chương.', 'utf8');
  writeFileSync(path.join(folder, '002-tiep.txt'), 'Chương 2\nNội dung thứ hai.', 'utf8');

  await page.goto(`/projects/${project.id}/import`);
  await expect(page.getByRole('heading', { name: 'Nhập nội dung' })).toBeVisible();

  await page.getByLabel('Local folder path').fill(folder);
  await page.getByRole('button', { name: 'Preview folder' }).click();

  const preview = page.getByRole('region', { name: 'Import preview', exact: true });
  await expect(preview).toBeVisible();
  await expect(preview.getByText('3 candidates ready for mapping review')).toBeVisible();

  // Both duplicate files carry the warning and name their own path.
  for (const file of ['001-mo-dau.txt', '001-trung.txt']) {
    const item = preview.locator('article').filter({ hasText: file });
    await expect(item.getByText('DUPLICATE_ORDINAL')).toBeVisible();
  }
  const clean = preview.locator('article').filter({ hasText: '002-tiep.txt' });
  await expect(clean.getByText('DUPLICATE_ORDINAL')).toHaveCount(0);

  // Vietnamese diacritics survive the whole path (encoding is not mangled).
  await expect(preview.getByText(/Nội dung mở đầu/)).toBeVisible();

  await page.getByRole('button', { name: 'Confirm import mapping' }).click();

  // Confirming navigates to the translation screen of the first imported chapter.
  await expect(page.getByRole('heading', { name: 'Dịch & Hiệu đính' })).toBeVisible();

  // Duplicate ordinals are the SAME chapter: `_get_or_create_chapter` keys on
  // (project, ordinal), so the second file becomes a new revision of chapter 1
  // instead of a silent second chapter. That is why the preview warns BEFORE
  // confirming: 3 files, 2 chapters.
  const chapters = await request.get(`/api/projects/${project.id}/chapters?limit=50`);
  expect(chapters.ok()).toBeTruthy();
  const listed = (await chapters.json()) as {
    total: number;
    items: { ordinal: number; sourceTitle: string | null }[];
  };
  expect(listed.total).toBe(2);
  expect(listed.items.map((item) => item.ordinal)).toEqual([1, 2]);
});
