import { expect, test } from '@playwright/test';

const origin = 'http://127.0.0.1:8765';

/**
 * A05 — the master is served over real HTTP Range requests and the browser player
 * consumes that URL directly (never a full Blob).
 *
 * This spec drives the fake/offline path on a real browser and a real server:
 * translation -> single narrator render -> audio screen, then asserts the HTTP
 * contract (200/206/416 + HEAD + ETag + path confinement) against the artifact
 * route the <audio> element actually uses.
 *
 * Label honesty: the fake TTS proves the Range/review plumbing, NOT VieNeu audio
 * quality. Listening checks against a real model stay NOT_RUN (no model/license).
 */
test('A05: artifact content is served by HTTP Range and the player uses that URL', async ({
  page,
  request,
}) => {
  const tokenResponse = await request.get('/api/security/bootstrap');
  const token = (await tokenResponse.json()) as { csrfToken: string };
  const headers = { Origin: origin, 'X-CSRF-Token': token.csrfToken };

  const projectResponse = await request.post('/api/projects', {
    headers,
    data: {
      title: 'Range mẫu',
      slug: `range-mau-e2e-${Date.now()}`,
      source_type: 'SELF_AUTHORED',
      rights_status: 'CLEARED',
    },
  });
  expect(projectResponse.ok()).toBeTruthy();
  const project = (await projectResponse.json()) as { id: string };

  for (const scope of ['TRANSLATE_VI', 'CREATE_AUDIO']) {
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
  const chapterId = imported.chapters[0].id;

  await page.goto(`/chapters/${chapterId}/translation`);
  await page.getByRole('button', { name: 'Dịch convert nội bộ' }).click();
  await expect(page.getByText('Đã dịch hoàn tất')).toBeVisible();
  await page.getByRole('button', { name: /Phê duyệt chuẩn/ }).click();
  await expect(page.getByRole('heading', { name: 'Giọng đọc' })).toBeVisible();
  await page.getByRole('button', { name: 'Render một giọng' }).click();
  await expect(page.getByRole('heading', { name: 'Audio' })).toBeVisible();

  // The player exists in the real page and points at the artifact content route.
  const audio = page.getByTestId('master-audio');
  await expect(audio).toBeAttached();
  await expect(audio).toHaveAttribute('controls', '');
  const src = await audio.getAttribute('src');
  expect(src).toMatch(new RegExp(`^/api/chapters/${chapterId}/audio/artifacts/[0-9a-f-]+/content$`));
  expect(src?.startsWith('blob:')).toBe(false);
  await expect(page.getByRole('region', { name: 'Nghe master' })).toBeVisible();

  const status = await request.get(`/api/chapters/${chapterId}/audio/status`);
  const master = (await status.json()) as { masterArtifactId: string; masterSha256: string };
  expect(src).toBe(`/api/chapters/${chapterId}/audio/artifacts/${master.masterArtifactId}/content`);

  const contentUrl = `${origin}${src}`;

  // Full body: 200 + range-capable headers + ETag from the stored checksum.
  const full = await request.get(contentUrl);
  expect(full.status()).toBe(200);
  expect(full.headers()['accept-ranges']).toBe('bytes');
  expect(full.headers()['etag']).toBe(`"${master.masterSha256}"`);
  const wholeBody = await full.body();
  expect(wholeBody.byteLength).toBeGreaterThan(0);

  // HEAD returns the same metadata without a body.
  const head = await request.head(contentUrl);
  expect(head.status()).toBe(200);
  expect(head.headers()['accept-ranges']).toBe('bytes');
  expect(head.headers()['content-length']).toBe(String(wholeBody.byteLength));

  // Partial content: the browser player relies on exactly this.
  const ranged = await request.get(contentUrl, { headers: { Range: 'bytes=0-9' } });
  expect(ranged.status()).toBe(206);
  expect(ranged.headers()['content-range']).toBe(`bytes 0-9/${wholeBody.byteLength}`);
  const rangedBody = await ranged.body();
  expect(rangedBody.byteLength).toBe(10);
  expect(rangedBody.equals(wholeBody.subarray(0, 10))).toBe(true);

  // Open-ended and suffix ranges.
  const suffix = await request.get(contentUrl, { headers: { Range: 'bytes=-8' } });
  expect(suffix.status()).toBe(206);
  expect((await suffix.body()).equals(wholeBody.subarray(wholeBody.byteLength - 8))).toBe(true);

  // Conditional request: unchanged artifact answers 304.
  const conditional = await request.get(contentUrl, {
    headers: { 'If-None-Match': `"${master.masterSha256}"` },
  });
  expect(conditional.status()).toBe(304);

  // Unsatisfiable range is rejected as 416 with the total size.
  const invalid = await request.get(contentUrl, {
    headers: { Range: `bytes=${wholeBody.byteLength + 10}-` },
  });
  expect(invalid.status()).toBe(416);
  expect(invalid.headers()['content-range']).toBe(`bytes */${wholeBody.byteLength}`);

  // No artifact route leaks an id from another chapter.
  const foreign = await request.get(
    `${origin}/api/chapters/018f0000-0000-7000-8000-0000000000ff/audio/artifacts/${master.masterArtifactId}/content`,
  );
  expect(foreign.status()).toBe(404);

  // Approval still requires the checksum: a stale hash is refused with 409.
  const staleApprove = await request.post(`/api/chapters/${chapterId}/audio/approve`, {
    headers,
    data: { masterArtifactId: master.masterArtifactId, expectedSha256: 'f'.repeat(64) },
  });
  expect(staleApprove.status()).toBe(409);

  // The user can seek with the keyboard and then approve the current master.
  await page.getByRole('region', { name: 'Nghe master' }).focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByTestId('playback-position')).toHaveText('0:05');
  await page.getByRole('button', { name: 'Phê duyệt audio' }).click();
  await expect(page.getByTestId('export-workflow')).toBeVisible();
});
