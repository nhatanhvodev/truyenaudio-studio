import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { StorageSettings } from './StorageSettings';

type Call = { url: string; init?: RequestInit };

function jsonResponse(body: unknown, status = 200): Response {
  const text = JSON.stringify(body);
  return { ok: status < 400, status, text: async () => text, json: async () => body } as Response;
}

function mockFetch(handler: (url: string, init?: RequestInit) => Response) {
  const calls: Call[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith('/api/security/bootstrap')) {
        return jsonResponse({ csrfToken: 'test-token' });
      }
      calls.push({ url, init });
      return handler(url, init);
    }),
  );
  return calls;
}

const DISK = {
  allowed: true,
  level: 'OK',
  usedBytes: 5 * 1024 * 1024,
  freeBytes: 100 * 1024 * 1024,
  estimatedBytes: 0,
  reasons: [],
};

const BACKUPS = {
  backups: [
    { id: 'backup-ok', sha256: 'a'.repeat(64), byteSize: 2048, verified: true, verificationError: null },
    { id: 'backup-bad', sha256: '', byteSize: 0, verified: false, verificationError: 'backup file is missing' },
  ],
};

function baseHandler(url: string, init?: RequestInit): Response {
  if (url === '/api/storage/disk') {
    return jsonResponse(DISK);
  }
  if (url === '/api/storage/backups') {
    return jsonResponse(BACKUPS, url === '/api/storage/backups' && (init?.method ?? 'GET') === 'GET' ? 200 : 200);
  }
  if (url.startsWith('/api/storage/retention?')) {
    return jsonResponse({
      requestedCount: 1,
      currentCount: 2,
      kept: ['backup-ok'],
      deletable: ['backup-bad'],
      applied: false,
      requiresConfirmation: true,
      pruneOnCreate: true,
    });
  }
  if (url === '/api/storage/cleanup/preview') {
    return jsonResponse({
      planId: 'plan-1',
      snapshotHash: 'b'.repeat(64),
      totalBytes: 4096,
      candidates: [{ candidateType: 'ARTIFACT', relativePath: 'audio/old.mp3', byteSize: 4096, sha256: 'c'.repeat(64) }],
    });
  }
  if (url === '/api/storage/cleanup/execute') {
    return jsonResponse({ planId: 'plan-1', deletedCount: 1, skippedCount: 0, freedBytes: 4096, deletedPaths: [], skippedPaths: [] });
  }
  if (url.endsWith('/restore-copy')) {
    return jsonResponse({ backupId: 'backup-ok', targetPath: 'D:/copy/studio.sqlite3', integrityCheck: 'ok', artifactPointerCount: 1 });
  }
  throw new Error(`unexpected url ${url}`);
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('StorageSettings (U02/U10)', () => {
  it('shows disk usage and each backup verification state', async () => {
    mockFetch(baseHandler);

    render(<StorageSettings />);

    expect(await screen.findByLabelText('Dung lượng')).toHaveTextContent('dùng 5.0 MB');
    expect(screen.getByText('backup-ok')).toBeVisible();
    expect(screen.getByText('checksum OK')).toBeVisible();
    expect(screen.getByText('checksum lỗi')).toBeVisible();
    expect(screen.getByText('backup file is missing')).toBeVisible();
  });

  it('creates a retention plan without deleting anything', async () => {
    const calls = mockFetch(baseHandler);

    render(<StorageSettings />);
    await screen.findByText('backup-ok');

    fireEvent.change(screen.getByLabelText('Số bản sao giữ lại'), { target: { value: '1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Xem trước retention' }));

    const plan = await screen.findByLabelText('Kế hoạch retention');
    expect(plan).toHaveTextContent('Giữ 1/2');
    expect(plan).toHaveTextContent('sẽ xoá 1');
    expect(plan).toHaveTextContent('chưa áp dụng');
    // A retention preview must not call any destructive endpoint.
    expect(calls.some((call) => call.url.includes('cleanup/execute'))).toBe(false);
    expect(calls.every((call) => (call.init?.method ?? 'GET') === 'GET')).toBe(true);
  });

  it('requires the user to review the cleanup plan before deleting', async () => {
    const calls = mockFetch(baseHandler);

    render(<StorageSettings />);
    await screen.findByText('backup-ok');

    fireEvent.click(screen.getByRole('button', { name: 'Xem trước dọn dẹp' }));

    const preview = await screen.findByLabelText('Cleanup preview');
    expect(preview).toHaveTextContent('audio/old.mp3');
    expect(calls.some((call) => call.url.includes('cleanup/execute'))).toBe(false);

    fireEvent.click(screen.getByRole('button', { name: 'Delete reviewed files' }));

    await waitFor(() => expect(screen.getByText('Đã xoá đúng các tệp trong kế hoạch đã xem.')).toBeVisible());
    const execute = calls.find((call) => call.url.includes('cleanup/execute'));
    expect(JSON.parse(String(execute?.init?.body))).toEqual({
      planId: 'plan-1',
      snapshotHash: 'b'.repeat(64),
    });
  });

  it('only enables a copy restore when the destination is typed back exactly', async () => {
    const calls = mockFetch(baseHandler);

    render(<StorageSettings />);
    await screen.findByText('backup-ok');

    // Only the verified backup offers a restore action.
    const restoreButton = screen.getByRole('button', { name: 'Phục hồi backup-ok vào bản sao' });
    expect(restoreButton).toBeDisabled();

    fireEvent.change(screen.getByLabelText('Thư mục đích'), { target: { value: 'D:/copy' } });
    fireEvent.change(screen.getByLabelText('Xác nhận thư mục đích'), { target: { value: 'D:/copy-typo' } });
    expect(restoreButton).toBeDisabled();
    expect(screen.getByText(/Cần gõ lại đúng thư mục đích/)).toBeVisible();

    fireEvent.change(screen.getByLabelText('Xác nhận thư mục đích'), { target: { value: 'D:/copy' } });
    expect(restoreButton).toBeEnabled();

    fireEvent.click(restoreButton);

    await waitFor(() =>
      expect(screen.getByText('Đã phục hồi bản sao vào thư mục mới; dữ liệu gốc không bị thay đổi.')).toBeVisible(),
    );
    const restore = calls.find((call) => call.url.endsWith('/restore-copy'));
    expect(restore?.url).toBe('/api/storage/backups/backup-ok/restore-copy');
    expect(JSON.parse(String(restore?.init?.body))).toEqual({
      targetDataRoot: 'D:/copy',
      confirmTarget: 'D:/copy',
    });
  });

  it('surfaces backend refusals instead of pretending success', async () => {
    mockFetch((url, init) => {
      if (url === '/api/storage/disk') {
        return jsonResponse(DISK);
      }
      if (url === '/api/storage/backups') {
        return jsonResponse(BACKUPS);
      }
      if (url.endsWith('/restore-copy')) {
        return jsonResponse({ detail: 'RESTORE_TARGET_CONFIRMATION_REQUIRED' }, 400);
      }
      return baseHandler(url, init);
    });

    render(<StorageSettings />);
    await screen.findByText('backup-ok');

    fireEvent.change(screen.getByLabelText('Thư mục đích'), { target: { value: 'D:/copy' } });
    fireEvent.change(screen.getByLabelText('Xác nhận thư mục đích'), { target: { value: 'D:/copy' } });
    fireEvent.click(screen.getByRole('button', { name: 'Phục hồi backup-ok vào bản sao' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('RESTORE_TARGET_CONFIRMATION_REQUIRED');
  });
});
