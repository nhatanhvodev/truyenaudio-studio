import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import DraftControls from './DraftControls';

type Call = { url: string; init?: RequestInit };

function jsonResponse(body: unknown, status = 200): Response {
  const text = JSON.stringify(body);
  return {
    ok: status < 400,
    status,
    text: async () => text,
    json: async () => body,
  } as Response;
}

function mockFetch(handler: (url: string, init?: RequestInit) => Response) {
  const calls: Call[] = [];
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/api/security/bootstrap')) {
      return jsonResponse({ csrfToken: 'test-token' });
    }
    calls.push({ url, init });
    return handler(url, init);
  });
  vi.stubGlobal('fetch', fn);
  return calls;
}

function Harness({ baseRevisionId = 'rev-1' }: { baseRevisionId?: string | null }) {
  const [content, setContent] = useState<Record<string, string>>({ 'seg-1': 'Bản gốc' });
  return (
    <div>
      <DraftControls
        chapterId="chapter-1"
        baseRevisionId={baseRevisionId}
        content={content}
        onRestore={(restored) => setContent((current) => ({ ...current, ...restored }))}
      />
      <textarea
        aria-label="Đoạn seg-1"
        value={content['seg-1'] ?? ''}
        onChange={(event) => setContent({ 'seg-1': event.target.value })}
      />
    </div>
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('DraftControls (U04 round 2)', () => {
  it('restores the server draft on mount and keeps the revision for compare-and-swap', async () => {
    const calls = mockFetch((url) => {
      if (url.startsWith('/api/chapters/chapter-1/draft?')) {
        expect(url).toContain('baseRevisionId=rev-1');
        return jsonResponse({ draft: { revision: 3, content: { 'seg-1': 'Nháp máy chủ' } } });
      }
      throw new Error(`unexpected url ${url}`);
    });

    render(<Harness />);

    await waitFor(() => expect(screen.getByLabelText('Đoạn seg-1')).toHaveValue('Nháp máy chủ'));
    expect(screen.getByText('Nháp: bản 3')).toBeVisible();
    expect(calls[0].url).toContain('/api/chapters/chapter-1/draft?baseRevisionId=rev-1');
  });

  it('saves with the restored revision as the expectation and adopts the new revision', async () => {
    let saved: unknown = null;
    mockFetch((url, init) => {
      if (url.startsWith('/api/chapters/chapter-1/draft?')) {
        return jsonResponse({ draft: { revision: 2, content: { 'seg-1': 'Nháp cũ' } } });
      }
      if (url === '/api/chapters/chapter-1/draft') {
        saved = JSON.parse(String(init?.body));
        expect(new Headers(init?.headers).get('X-CSRF-Token')).toBe('test-token');
        return jsonResponse({ draft: { revision: 4, content: { 'seg-1': 'Bản gốc' } } });
      }
      throw new Error(`unexpected url ${url}`);
    });

    render(<Harness />);

    await waitFor(() => expect(screen.getByText('Nháp: bản 2')).toBeVisible());
    fireEvent.change(screen.getByLabelText('Đoạn seg-1'), { target: { value: 'Sửa tại chỗ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Lưu nháp' }));

    await waitFor(() => expect(screen.getByText('Đã lưu nháp')).toBeVisible());
    expect(saved).toEqual({
      base_revision_id: 'rev-1',
      content: { 'seg-1': 'Sửa tại chỗ' },
      expected_revision: 2,
    });
    expect(screen.getByText('Nháp: bản 4')).toBeVisible();
  });

  it('surfaces a conflict with the segment diff and keeps the local text', async () => {
    mockFetch((url, init) => {
      if (url.startsWith('/api/chapters/chapter-1/draft?')) {
        return jsonResponse({ draft: { revision: 1, content: { 'seg-1': 'Bản cũ' } } });
      }
      if (url === '/api/chapters/chapter-1/draft') {
        if ((init?.method ?? 'GET') === 'PUT') {
          return jsonResponse({ detail: 'DRAFT_REVISION_CONFLICT' }, 409);
        }
      }
      throw new Error(`unexpected url ${url}`);
    });

    render(<Harness />);

    await waitFor(() => expect(screen.getByText('Nháp: bản 1')).toBeVisible());
    fireEvent.change(screen.getByLabelText('Đoạn seg-1'), { target: { value: 'Bản của tôi' } });
    fireEvent.click(screen.getByRole('button', { name: 'Lưu nháp' }));

    await waitFor(() => expect(screen.getByLabelText('Xung đột bản nháp')).toBeVisible());
    expect(screen.getByText(/Bản đang sửa của bạn vẫn được giữ nguyên/)).toBeVisible();
    // The diff names the segment and both sides, and the local text is intact.
    expect(screen.getByText(/Đoạn seg-1/)).toBeVisible();
    expect(screen.getByLabelText('Đoạn seg-1')).toHaveValue('Bản của tôi');
  });

  it('treats a lost race with identical content as an idempotent retry', async () => {
    mockFetch((url, init) => {
      if (url.startsWith('/api/chapters/chapter-1/draft?')) {
        return jsonResponse({ draft: { revision: 5, content: { 'seg-1': 'Bản gốc' } } });
      }
      if (url === '/api/chapters/chapter-1/draft' && (init?.method ?? 'GET') === 'PUT') {
        return jsonResponse({ detail: 'DRAFT_REVISION_CONFLICT' }, 409);
      }
      throw new Error(`unexpected url ${url}`);
    });

    render(<Harness />);

    await waitFor(() => expect(screen.getByText('Nháp: bản 5')).toBeVisible());
    fireEvent.click(screen.getByRole('button', { name: 'Lưu nháp' }));

    await waitFor(() => expect(screen.getByText('Nháp đã đồng bộ với máy chủ')).toBeVisible());
    expect(screen.queryByLabelText('Xung đột bản nháp')).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('overwrites with the server revision when the user chooses their own text', async () => {
    const puts: unknown[] = [];
    mockFetch((url, init) => {
      if (url.startsWith('/api/chapters/chapter-1/draft?')) {
        return jsonResponse({ draft: { revision: 1, content: { 'seg-1': 'Bản cũ' } } });
      }
      if (url === '/api/chapters/chapter-1/draft' && (init?.method ?? 'GET') === 'PUT') {
        puts.push(JSON.parse(String(init?.body)));
        if (puts.length === 1) {
          return jsonResponse({ detail: 'DRAFT_REVISION_CONFLICT' }, 409);
        }
        return jsonResponse({ draft: { revision: 2, content: { 'seg-1': 'Bản của tôi' } } });
      }
      throw new Error(`unexpected url ${url}`);
    });

    render(<Harness />);

    await waitFor(() => expect(screen.getByText('Nháp: bản 1')).toBeVisible());
    fireEvent.change(screen.getByLabelText('Đoạn seg-1'), { target: { value: 'Bản của tôi' } });
    fireEvent.click(screen.getByRole('button', { name: 'Lưu nháp' }));
    await waitFor(() => expect(screen.getByLabelText('Xung đột bản nháp')).toBeVisible());

    fireEvent.click(screen.getByRole('button', { name: 'Ghi đè bằng bản của tôi' }));

    await waitFor(() => expect(screen.getByText('Nháp: bản 2')).toBeVisible());
    expect(puts[1]).toEqual({
      base_revision_id: 'rev-1',
      content: { 'seg-1': 'Bản của tôi' },
      expected_revision: 1,
    });
    expect(screen.queryByLabelText('Xung đột bản nháp')).not.toBeInTheDocument();
  });

  it('uses the server text when the user discards the local draft', async () => {
    mockFetch((url, init) => {
      if (url.startsWith('/api/chapters/chapter-1/draft?')) {
        return jsonResponse({ draft: { revision: 1, content: { 'seg-1': 'Bản cũ' } } });
      }
      if (url === '/api/chapters/chapter-1/draft' && (init?.method ?? 'GET') === 'PUT') {
        return jsonResponse({ detail: 'DRAFT_REVISION_CONFLICT' }, 409);
      }
      throw new Error(`unexpected url ${url}`);
    });

    render(<Harness />);

    await waitFor(() => expect(screen.getByText('Nháp: bản 1')).toBeVisible());
    fireEvent.change(screen.getByLabelText('Đoạn seg-1'), { target: { value: 'Bản của tôi' } });
    fireEvent.click(screen.getByRole('button', { name: 'Lưu nháp' }));
    await waitFor(() => expect(screen.getByLabelText('Xung đột bản nháp')).toBeVisible());

    fireEvent.click(screen.getByRole('button', { name: 'Dùng bản trên máy chủ' }));

    await waitFor(() => expect(screen.getByLabelText('Đoạn seg-1')).toHaveValue('Bản cũ'));
    expect(screen.queryByLabelText('Xung đột bản nháp')).not.toBeInTheDocument();
  });

  it('keeps unsaved text and reports the error when the save fails', async () => {
    mockFetch((url, init) => {
      if (url.startsWith('/api/chapters/chapter-1/draft?')) {
        return jsonResponse({ draft: null });
      }
      if (url === '/api/chapters/chapter-1/draft' && (init?.method ?? 'GET') === 'PUT') {
        return jsonResponse({ detail: 'DRAFT_TOO_LARGE' }, 400);
      }
      throw new Error(`unexpected url ${url}`);
    });

    render(<Harness />);

    await waitFor(() => expect(screen.getByText('Nháp: chưa có')).toBeVisible());
    fireEvent.change(screen.getByLabelText('Đoạn seg-1'), { target: { value: 'Rất dài' } });
    fireEvent.click(screen.getByRole('button', { name: 'Lưu nháp' }));

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('DRAFT_TOO_LARGE'));
    expect(screen.getByLabelText('Đoạn seg-1')).toHaveValue('Rất dài');
  });

  it('disables draft storage when there is no active source revision', () => {
    mockFetch(() => {
      throw new Error('draft API must not be called without a base revision');
    });

    render(<Harness baseRevisionId={null} />);

    expect(screen.getByText(/Chưa có bản nguồn đang hoạt động/)).toBeVisible();
  });
});
