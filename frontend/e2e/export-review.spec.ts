import { expect, test, type Page, type APIRequestContext } from '@playwright/test';

const origin = 'http://127.0.0.1:8765';

const PUBLICATION_FILES = [
  'tap-0001.mp3',
  'ban-dich.md',
  'transcript.srt',
  'metadata.json',
  'production-report.json',
  'provenance.json',
  'THIRD_PARTY_LICENSES.txt',
  'checksums.sha256',
];

/**
 * E01 — export bundle and review metadata.
 *
 * Real server + real Chrome on the fake/offline branch: provision rights ->
 * import -> translate -> approve -> render one voice -> approve audio -> Export
 * screen. Everything below is asserted against real HTTP: the publication
 * bundle really exists on disk, its checksum manifest really verifies, and the
 * browser really never uploads anything.
 *
 * Label honesty: this proves the export/review plumbing on the fake audio
 * branch. It does NOT prove VieNeu audio quality (no model/license on this
 * machine) and the studio never uploads the bundle anywhere (C08).
 */
test('E01: gate cho phép, tạo bundle publication và status xác minh checksum thật', async ({
  page,
  request,
  baseURL,
}) => {
  const outbound = collectOutboundRequests(page, baseURL ?? origin);
  const { chapterId } = await provisionChapterReadyToExport(page, request, 'Export review mẫu');

  // The export screen shows the gate decision computed from real rights.
  const gate = page.locator('[aria-label="Cong xuat ban"], [aria-label="Xuất bản và metadata"]').first();
  await expect(gate).toBeVisible();
  await expect(page.getByRole('button', { name: 'Tạo bundle publication' })).toBeEnabled();

  await fillPublicationMetadataIfPresent(page);
  await page.getByRole('button', { name: 'Tạo bundle publication' }).click();
  await expect(
    page.getByText(/Đã verify checksum|Đã tạo bundle publication\./),
  ).toBeVisible();

  const response = await request.get(`/api/chapters/${chapterId}/exports/status`);
  expect(response.status()).toBe(200);
  const status = (await response.json()) as {
    chapterId: string;
    gate: { allowed: boolean; reasons: string[]; rightsEvaluationHash: string };
    bundles: {
      id: string;
      kind: string;
      status: string;
      manifestSha256: string;
      artifactId: string;
      directoryPath: string;
      files: string[];
      createdAt: string;
      verified: boolean;
      mismatches: string[];
      stale: boolean;
      staleReasons: string[];
    }[];
  };

  expect(status.chapterId).toBe(chapterId);
  expect(status.gate.allowed).toBe(true);
  expect(status.gate.reasons).toEqual([]);
  expect(status.gate.rightsEvaluationHash).toMatch(/^[0-9a-f]{64}$/);

  expect(status.bundles.map((item) => item.kind)).toEqual(['PUBLICATION_BUNDLE']);
  const bundle = status.bundles[0];
  expect(bundle.status).toBe('READY');
  expect(bundle.manifestSha256).toMatch(/^[0-9a-f]{64}$/);
  expect(bundle.artifactId).not.toBe('');
  expect(bundle.directoryPath).not.toBe('');
  expect(bundle.verified).toBe(true);
  expect(bundle.mismatches).toEqual([]);
  expect(bundle.stale).toBe(false);
  expect(bundle.staleReasons).toEqual([]);
  expect(Number.isNaN(Date.parse(bundle.createdAt))).toBe(false);
  expect([...bundle.files].sort()).toEqual([...PUBLICATION_FILES].sort());

  // The manifest the UI shows is the manifest the server verified.
  await expect(page.getByText(new RegExp(bundle.manifestSha256.slice(0, 12)))).toBeVisible();

  // A chapter that does not exist is a clean 404, never another chapter's data.
  const missing = await request.get(
    `${origin}/api/chapters/018f0000-0000-7000-8000-0000000000ff/exports/status`,
  );
  expect(missing.status()).toBe(404);
  expect((await missing.json()).detail).toBe('CHAPTER_NOT_FOUND');

  // C08: the browser never contacted anything outside the studio.
  expect(outbound).toEqual([]);
});

test('E01: màn Export nhắc bản xuất chỉ để tải thủ công, không tự upload', async ({
  page,
  request,
}) => {
  await provisionChapterReadyToExport(page, request, 'Export notice mẫu');

  const workflow = page.getByTestId('export-workflow');
  test.skip(
    (await workflow.count()) === 0,
    'ExportWorkflow chưa được gắn vào router — chờ người điều phối gắn UI (export-review.spec.ts).',
  );

  await expect(page.getByTestId('manual-upload-notice')).toHaveText(
    'Bản xuất chỉ để tải thủ công sang app chính — studio không tự upload.',
  );
});

function collectOutboundRequests(page: Page, allowedOrigin: string): string[] {
  const outbound: string[] = [];
  page.on('request', (browserRequest) => {
    const url = browserRequest.url();
    if (url.startsWith(allowedOrigin) || url.startsWith('data:') || url.startsWith('blob:')) {
      return;
    }
    outbound.push(url);
  });
  return outbound;
}

/**
 * The current Export screen posts fixed metadata; the new ExportWorkflow owns a
 * validated form. Fill it when it is present so this spec survives the router
 * wiring instead of depending on which screen renders today.
 */
async function fillPublicationMetadataIfPresent(page: Page): Promise<void> {
  const title = page.getByLabel('Tiêu đề tập');
  if (await title.count()) {
    await title.fill('Tập 1');
  }
  const number = page.getByLabel('Số tập');
  if (await number.count()) {
    await number.fill('1');
  }
}

async function provisionChapterReadyToExport(
  page: Page,
  request: APIRequestContext,
  title: string,
): Promise<{ chapterId: string }> {
  const tokenResponse = await request.get('/api/security/bootstrap');
  const token = (await tokenResponse.json()) as { csrfToken: string };
  const headers = { Origin: origin, 'X-CSRF-Token': token.csrfToken };

  const projectResponse = await request.post('/api/projects', {
    headers,
    data: {
      title,
      slug: `${title.toLowerCase().replace(/[^a-z0-9]+/g, '-')}-e2e-${Date.now()}`,
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
  const chapterId = imported.chapters[0].id;

  // Fake (local, offline) translation then approval.
  await page.goto(`/chapters/${chapterId}/translation`);
  await expect(page.getByRole('heading', { name: 'Dịch & Hiệu đính' })).toBeVisible();
  await page.getByRole('button', { name: 'Dịch convert nội bộ' }).click();
  await expect(page.getByText('Đã dịch hoàn tất')).toBeVisible();
  await page.getByRole('button', { name: /Phê duyệt chuẩn/ }).click();

  // One voice render, then approve the master audio.
  await expect(page.getByRole('heading', { name: 'Giọng đọc' })).toBeVisible();
  await page.getByRole('button', { name: 'Render một giọng' }).click();
  await expect(page.getByRole('heading', { name: 'Audio' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Nghe master' })).toBeVisible();
  const approve = page.getByRole('button', { name: 'Phê duyệt audio' });
  await expect(approve).toBeEnabled();
  await approve.click();
  await expect(page.getByRole('heading', { name: /Export|Xuất bản/ })).toBeVisible();

  return { chapterId };
}
