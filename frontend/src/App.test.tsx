import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.resetModules();
  window.history.replaceState(null, '', '/');
});

describe('App', () => {
  it('opens on the project and rights workflow instead of a landing page', async () => {
    const { default: App } = await import('./App');

    render(<App />);

    expect(await screen.findByRole('heading', { name: 'Truyện Audio Studio' })).toBeVisible();
    expect(screen.getByLabelText('Tên truyện')).toBeVisible();
    expect(screen.getByLabelText('Loại nguồn')).toBeVisible();
    expect(screen.getByLabelText('Trạng thái quyền')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Tạo dự án' })).toBeVisible();
  });

  it('keeps the jobs overlay available without browser EventSource support', async () => {
    vi.stubGlobal('EventSource', undefined);
    const { default: App } = await import('./App');

    render(<App />);

    expect(await screen.findByLabelText('Jobs overlay')).toBeVisible();
    expect(screen.getByText('Chưa có job đang chạy')).toBeVisible();
  });

  it('loads rendered audio approval state on direct navigation', async () => {
    window.history.replaceState(null, '', '/chapters/chapter-1/audio');
    vi.stubGlobal('EventSource', undefined);
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (url === '/api/chapters/chapter-1/audio/status') {
          return jsonResponse({
            chapterId: 'chapter-1',
            masterArtifactId: 'master-1',
            masterSha256: 'abc123abc123abc123abc123abc123abc123abc123abc123abc123abc123abcd',
            approved: false,
          });
        }
        if (url === '/api/jobs/snapshot') {
          return jsonResponse({ events: [] });
        }
        throw new Error(`unexpected url ${url}`);
      }),
    );
    const { default: App } = await import('./App');

    render(<App />);

    expect(await screen.findByText(/Master abc123abc123/)).toBeVisible();
    expect(screen.getByRole('button', { name: 'Phê duyệt audio' })).toBeVisible();
  });

  it('requires guard IDs before calling the Qwen translation route', async () => {
    window.history.replaceState(null, '', '/chapters/chapter-1/translation');
    vi.stubGlobal('EventSource', undefined);
    const fetchSpy = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/chapters/chapter-1/translation') {
        return jsonResponse({ detail: 'TRANSLATION_RUN_NOT_FOUND' }, false, 404);
      }
      if (url === '/api/security/bootstrap') {
        return jsonResponse({ csrfToken: 'token-1' });
      }
      if (url === '/api/chapters/chapter-1/translation/qwen' && init?.method === 'POST') {
        return jsonResponse(translationPayload('qwen-run-1'));
      }
      if (url === '/api/jobs/snapshot') {
        return jsonResponse({ events: [] });
      }
      throw new Error(`unexpected url ${url}`);
    });
    vi.stubGlobal('fetch', fetchSpy);
    const { default: App } = await import('./App');

    render(<App />);

    const qwenButton = await screen.findByRole('button', { name: 'Dịch bằng Qwen' });
    expect(screen.getByText('Qwen cần consent cloud và budget authorization đã tạo trước.')).toBeVisible();

    fireEvent.click(qwenButton);

    expect(await screen.findByRole('alert')).toHaveTextContent('CLOUD_CONSENT_AND_BUDGET_REQUIRED');
    expect(fetchSpy.mock.calls.some(([url]) => url === '/api/chapters/chapter-1/translation/qwen')).toBe(false);

    fireEvent.change(screen.getByLabelText('Cloud consent ID'), { target: { value: 'consent-1' } });
    fireEvent.change(screen.getByLabelText('Budget authorization ID'), { target: { value: 'budget-1' } });
    fireEvent.click(qwenButton);

    await waitFor(() => expect(fetchSpy).toHaveBeenCalledWith('/api/chapters/chapter-1/translation/qwen', expect.anything()));
    const qwenCall = fetchSpy.mock.calls.find(([url]) => url === '/api/chapters/chapter-1/translation/qwen');
    expect(JSON.parse(String(qwenCall?.[1]?.body))).toEqual({
      cloudConsentId: 'consent-1',
      budgetAuthorizationId: 'budget-1',
    });
    expect(await screen.findByText('Qwen source')).toBeVisible();
    expect(screen.getByText('Qwen target')).toBeVisible();
  });

  it('builds a private archive from the export screen and displays checksum result', async () => {
    window.history.replaceState(null, '', '/chapters/chapter-1/export');
    vi.stubGlobal('EventSource', undefined);
    const fetchSpy = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/chapters/chapter-1/exports/gate') {
        return jsonResponse({
          allowed: false,
          reasons: ['PUBLIC_STREAM'],
          rightsEvaluationHash: 'abc123abc123abc123abc123abc123abc123abc123abc123abc123abc123abcd',
        });
      }
      if (url === '/api/security/bootstrap') {
        return jsonResponse({ csrfToken: 'token-1' });
      }
      if (url === '/api/chapters/chapter-1/exports/private' && init?.method === 'POST') {
        return jsonResponse({
          id: 'export-private-1',
          files: ['PRIVATE_ONLY.txt', 'checksums.sha256'],
          manifestSha256: 'def456def456def456def456def456def456def456def456def456def456abcd',
          directoryPath: 'D:/tmp/private',
        });
      }
      if (url === '/api/jobs/snapshot') {
        return jsonResponse({ events: [] });
      }
      throw new Error(`unexpected url ${url}`);
    });
    vi.stubGlobal('fetch', fetchSpy);
    const { default: App } = await import('./App');

    render(<App />);
    fireEvent.click(await screen.findByRole('button', { name: 'Tao archive rieng tu' }));

    await waitFor(() => expect(fetchSpy).toHaveBeenCalledWith('/api/chapters/chapter-1/exports/private', expect.anything()));
    expect(await screen.findByText('Đã tạo archive riêng tư')).toBeVisible();
    expect(screen.getByText(/def456def456/)).toBeVisible();
  });
});

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return {
    ok,
    status,
    text: async () => JSON.stringify(body),
    json: async () => body,
  } as Response;
}

function translationPayload(id: string) {
  return {
    run: {
      id,
      status: 'REVIEW',
      sha256: 'abc123abc123abc123abc123abc123abc123abc123abc123abc123abc123abcd',
    },
    segments: [
      {
        sourceSegmentId: 'source-1',
        sourceText: 'Qwen source',
        targetText: 'Qwen target',
      },
    ],
  };
}
