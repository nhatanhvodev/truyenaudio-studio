import { expect, test } from '@playwright/test';
import zlib from 'node:zlib';

const origin = 'http://127.0.0.1:8765';
const fixtureSecret = 'FULL_SOURCE_SECRET_TASK7';

test('50 chapter batch list stays paged and fake recovery path exports clean diagnostics', async ({ page, request }) => {
  const tokenResponse = await request.get('/api/security/bootstrap');
  const token = (await tokenResponse.json()) as { csrfToken: string };
  const headers = { Origin: origin, 'X-CSRF-Token': token.csrfToken };

  const projectResponse = await request.post('/api/projects', {
    headers,
    data: {
      title: 'Batch Recovery E2E',
      slug: `batch-recovery-e2e-${Date.now()}`,
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
      items: Array.from({ length: 50 }, (_, index) => ({
        ordinal: index + 1,
        title: `Chương ${index + 1}`,
        text: `第${index + 1}章\n${fixtureSecret}_${index + 1}\n林动说：“你好。”`,
      })),
    },
  });
  expect(importResponse.ok()).toBeTruthy();
  const imported = (await importResponse.json()) as { chapters: { id: string }[] };
  const firstChapterId = imported.chapters[0].id;

  await page.goto(`/projects/${project.id}/batch`);
  await expect(page.getByLabel('Batch queue')).toBeVisible();
  await expect(page.getByText('Chương 1', { exact: true })).toBeVisible();
  await expect(page.getByText('Chương 25', { exact: true })).toBeVisible();
  await expect(page.getByText('Chương 26', { exact: true })).not.toBeVisible();
  await expect(page.getByText(fixtureSecret)).not.toBeVisible();
  await page.getByRole('button', { name: 'Load more' }).click();
  await expect(page.getByText('Chương 50', { exact: true })).toBeVisible();
  await expect(page.getByText(fixtureSecret)).not.toBeVisible();

  await page.goto(`/chapters/${firstChapterId}/translation`);
  await page.getByRole('button', { name: 'Dịch bằng fake' }).click();
  await expect(page.getByText('Chờ duyệt bản dịch')).toBeVisible();
  await page.getByRole('button', { name: 'Phê duyệt bản dịch' }).click();
  await page.getByRole('button', { name: 'Render một giọng' }).click();
  await page.getByRole('button', { name: 'Phê duyệt audio' }).click();
  await expect(page.getByText('Tạo bundle publication')).toBeVisible();
  await page.getByRole('button', { name: 'Tao archive rieng tu' }).click();
  await expect(page.getByText('Đã tạo archive riêng tư')).toBeVisible();
  await page.getByRole('button', { name: 'Tạo bundle publication' }).click();
  await expect(page.getByText('Đã verify checksum')).toBeVisible();

  const recoveryResponse = await request.post(`/api/diagnostics/fake-recovery/run?projectId=${project.id}`, {
    headers,
  });
  expect(recoveryResponse.ok()).toBeTruthy();
  const recovery = (await recoveryResponse.json()) as {
    workerRunCount: number;
    workerDrivenRecoveryCount: number;
    recoveredJobStatus: string;
    duplicateReadyCacheKeysAfter: string[];
    duplicateReadyExportManifestsAfter: string[];
    missingReadyArtifactCountAfter: number;
  };
  expect(recovery.workerRunCount).toBeGreaterThanOrEqual(2);
  expect(recovery.workerDrivenRecoveryCount).toBe(1);
  expect(recovery.recoveredJobStatus).toBe('SUCCEEDED');
  expect(recovery.duplicateReadyCacheKeysAfter).toEqual([]);
  expect(recovery.duplicateReadyExportManifestsAfter).toEqual([]);
  expect(recovery.missingReadyArtifactCountAfter).toBe(0);

  const diagnosticsResponse = await request.post('/api/diagnostics/export', {
    headers,
    data: { includeSample: false, sampleText: `${fixtureSecret} explicit sample` },
  });
  expect(diagnosticsResponse.ok()).toBeTruthy();
  const archiveText = unzipText(Buffer.from(await diagnosticsResponse.body()));
  expect(archiveText).not.toContain(fixtureSecret);
  expect(archiveText).toContain('health.json');
  expect(archiveText).toContain('config.json');
});

function unzipText(buffer: Buffer): string {
  let offset = 0;
  const chunks: string[] = [];
  while (offset < buffer.length - 30) {
    const signature = buffer.readUInt32LE(offset);
    if (signature !== 0x04034b50) {
      offset += 1;
      continue;
    }
    const compression = buffer.readUInt16LE(offset + 8);
    const compressedSize = buffer.readUInt32LE(offset + 18);
    const fileNameLength = buffer.readUInt16LE(offset + 26);
    const extraLength = buffer.readUInt16LE(offset + 28);
    const nameStart = offset + 30;
    const dataStart = nameStart + fileNameLength + extraLength;
    const fileName = buffer.subarray(nameStart, nameStart + fileNameLength).toString('utf8');
    const payload = buffer.subarray(dataStart, dataStart + compressedSize);
    chunks.push(fileName);
    if (compression === 0) {
      chunks.push(payload.toString('utf8'));
    }
    if (compression === 8) {
      chunks.push(zlib.inflateRawSync(payload).toString('utf8'));
    }
    offset = dataStart + compressedSize;
  }
  return chunks.join('\n');
}
