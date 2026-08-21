import { cleanup, render, screen } from '@testing-library/react';
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
});

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    text: async () => JSON.stringify(body),
    json: async () => body,
  } as Response;
}
