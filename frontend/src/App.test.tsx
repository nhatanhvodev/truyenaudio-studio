import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.resetModules();
  window.localStorage.clear();
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

  it('phát master bằng URL Range thay vì nạp Blob toàn bộ file (A05)', async () => {
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

    const audio = (await screen.findByTestId('master-audio')) as HTMLAudioElement;
    expect(audio.getAttribute('src')).toBe('/api/chapters/chapter-1/audio/artifacts/master-1/content');
    expect(audio.getAttribute('src')?.startsWith('blob:')).toBe(false);
    expect(audio.getAttribute('preload')).toBe('metadata');
    expect(screen.getByRole('region', { name: 'Nghe master' })).toBeVisible();
  });

  it('chặn phê duyệt và cảnh báo khi master trên máy chủ khác bản đang nghe (A05)', async () => {
    // React Router giữ location.state trong khoá "usr" của history.state.
    window.history.replaceState(
      {
        usr: {
          rendered: {
            masterArtifactId: 'master-old',
            masterSha256: 'old'.padEnd(64, '0'),
            reusedSegmentIds: [],
            renderedSegmentIds: [],
          },
        },
        key: 'a05-stale',
        idx: 0,
      },
      '',
      '/chapters/chapter-1/audio',
    );
    vi.stubGlobal('EventSource', undefined);
    const fetchSpy = vi.fn(async (url: string) => {
      if (url === '/api/chapters/chapter-1/audio/status') {
        return jsonResponse({
          chapterId: 'chapter-1',
          masterArtifactId: 'master-new',
          masterSha256: 'new'.padEnd(64, '0'),
          approved: false,
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

    expect(await screen.findByText(/Bản master trên máy chủ đã thay đổi/)).toBeVisible();
    expect(screen.getByRole('button', { name: 'Phê duyệt audio' })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: 'Nghe bản mới' }));

    expect(screen.getByRole('button', { name: 'Phê duyệt audio' })).toBeEnabled();
    expect(screen.queryByText(/Bản master trên máy chủ đã thay đổi/)).toBeNull();
    expect(
      fetchSpy.mock.calls.filter(([url]) => String(url).includes('/audio/approve')),
    ).toHaveLength(0);
  });

  it('mounts the A02 voice preview panel and keeps the chosen voice id', async () => {
    window.history.replaceState(null, '', '/chapters/chapter-1/voice');
    vi.stubGlobal('EventSource', undefined);
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (url === '/api/voices?locale=vi-VN') {
          return jsonResponse({
            previewText: 'Xin chào',
            voices: [
              { id: 'voice-1', name: 'Giọng 1', locale: 'vi-VN', available: true, active: true },
              { id: 'voice-2', name: 'Giọng 2', locale: 'vi-VN', available: true, active: false },
              {
                id: 'voice-3',
                name: 'Giọng 3',
                locale: 'vi-VN',
                available: false,
                active: false,
                activationHint: 'Cần cài model và license.',
              },
            ],
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

    expect(await screen.findByLabelText('Nghe thử và chọn giọng')).toBeVisible();
    expect(screen.getByText('Preset voice-1')).toBeVisible();

    const available = screen.getByText('Giọng 2').closest('li') as HTMLElement;
    fireEvent.click(within(available).getByRole('button', { name: 'Chọn giọng này' }));

    expect(screen.getByText('Preset voice-2')).toBeVisible();
    // Giọng chưa cài model/license: chỉ hiện hướng dẫn, không có điều khiển giả.
    const missing = screen.getByText('Giọng 3').closest('li') as HTMLElement;
    expect(within(missing).queryByRole('button', { name: 'Nghe thử' })).toBeNull();
    expect(within(missing).getByText('Cần cài model và license.')).toBeVisible();
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
    fireEvent.change(screen.getByLabelText('Qwen profile ID'), { target: { value: 'profile-qwen-1' } });
    fireEvent.click(qwenButton);

    await waitFor(() => expect(fetchSpy).toHaveBeenCalledWith('/api/chapters/chapter-1/translation/qwen', expect.anything()));
    const qwenCall = fetchSpy.mock.calls.find(([url]) => url === '/api/chapters/chapter-1/translation/qwen');
    expect(JSON.parse(String(qwenCall?.[1]?.body))).toEqual({
      profileId: 'profile-qwen-1',
      cloudConsentId: 'consent-1',
      budgetAuthorizationId: 'budget-1',
    });
    expect(await screen.findByText('Qwen source')).toBeVisible();
    expect(screen.getByText('Qwen target')).toBeVisible();
  });

  it('removes a legacy Gemini credential while bootstrapping the root route', async () => {
    window.localStorage.setItem('gemini_api_key', 'legacy-secret');
    const { default: App } = await import('./App');

    render(<App />);

    await waitFor(() => expect(window.localStorage.getItem('gemini_api_key')).toBeNull());
  });

  it('sends a credential only to provisioning, never inference', async () => {
    window.history.replaceState(null, '', '/chapters/chapter-1/translation');
    vi.stubGlobal('EventSource', undefined);
    const fetchSpy = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/chapters/chapter-1/translation') return jsonResponse({ detail: 'TRANSLATION_RUN_NOT_FOUND' }, false, 404);
      if (url === '/api/security/bootstrap') return jsonResponse({ csrfToken: 'token-1' });
      if (url === '/api/cloud-profiles/profile-gemini-1/credential' && init?.method === 'PUT') return jsonResponse({ secretConfigured: true });
      if (url === '/api/chapters/chapter-1/translation/gemini' && init?.method === 'POST') return jsonResponse(translationPayload('gemini-run-1'));
      if (url === '/api/jobs/snapshot') return jsonResponse({ events: [] });
      throw new Error(`unexpected url ${url}`);
    });
    vi.stubGlobal('fetch', fetchSpy);
    const { default: App } = await import('./App');
    render(<App />);

    expect(window.localStorage.getItem('gemini_api_key')).toBeNull();
    fireEvent.change(await screen.findByLabelText('Gemini profile ID'), { target: { value: 'profile-gemini-1' } });
    fireEvent.change(screen.getByPlaceholderText(/Dán AIzaSy/), { target: { value: 'transient-secret' } });
    fireEvent.click(screen.getByRole('button', { name: 'Lưu API key vào profile' }));
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledWith('/api/cloud-profiles/profile-gemini-1/credential', expect.anything()));
    const provisionCall = fetchSpy.mock.calls.find(([url]) => url === '/api/cloud-profiles/profile-gemini-1/credential');
    expect(JSON.parse(String(provisionCall?.[1]?.body))).toEqual({ secret: 'transient-secret' });
    expect(screen.getByPlaceholderText(/Dán AIzaSy/)).toHaveValue('');

    fireEvent.change(screen.getByLabelText('Cloud consent ID'), { target: { value: 'consent-1' } });
    fireEvent.change(screen.getByLabelText('Budget authorization ID'), { target: { value: 'budget-1' } });
    fireEvent.click(screen.getByRole('button', { name: /Dịch toàn bộ chương bằng Gemini AI/ }));
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledWith('/api/chapters/chapter-1/translation/gemini', expect.anything()));
    const inferenceCall = fetchSpy.mock.calls.find(([url]) => url === '/api/chapters/chapter-1/translation/gemini');
    expect(JSON.parse(String(inferenceCall?.[1]?.body))).toEqual({
      profileId: 'profile-gemini-1', cloudConsentId: 'consent-1', budgetAuthorizationId: 'budget-1',
    });
    expect(String(inferenceCall?.[1]?.body)).not.toContain('secret');
  });

  it('clears a transient Gemini credential after failed provisioning', async () => {
    window.history.replaceState(null, '', '/chapters/chapter-1/translation');
    vi.stubGlobal('EventSource', undefined);
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/chapters/chapter-1/translation') return jsonResponse({ detail: 'TRANSLATION_RUN_NOT_FOUND' }, false, 404);
      if (url === '/api/security/bootstrap') return jsonResponse({ csrfToken: 'token-1' });
      if (url === '/api/cloud-profiles/profile-gemini-1/credential' && init?.method === 'PUT') return jsonResponse({ detail: 'KEYRING_UNAVAILABLE' }, false, 503);
      if (url === '/api/jobs/snapshot') return jsonResponse({ events: [] });
      throw new Error(`unexpected url ${url}`);
    }));
    const { default: App } = await import('./App');
    render(<App />);

    fireEvent.change(await screen.findByLabelText('Gemini profile ID'), { target: { value: 'profile-gemini-1' } });
    fireEvent.change(screen.getByPlaceholderText(/Dán AIzaSy/), { target: { value: 'failed-transient-secret' } });
    fireEvent.click(screen.getByRole('button', { name: 'Lưu API key vào profile' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('KEYRING_UNAVAILABLE');
    expect(screen.getByPlaceholderText(/Dán AIzaSy/)).toHaveValue('');
  });

  it('previews folder imports and confirms only after mapping review', async () => {
    window.history.replaceState(null, '', '/projects/project-1/import');
    vi.stubGlobal('EventSource', undefined);
    const fetchSpy = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/projects/project-1/chapters/import/preview' && init?.method === 'POST') {
        return jsonResponse({
          candidates: [
            {
              ordinal: 1,
              title: 'Chuong 1',
              text: 'Chuong 1\nPreview body.',
              sourcePath: 'chapter-1.txt',
              warnings: [],
            },
          ],
        });
      }
      if (url === '/api/security/bootstrap') {
        return jsonResponse({ csrfToken: 'token-1' });
      }
      if (url === '/api/projects/project-1/chapters/import' && init?.method === 'POST') {
        return jsonResponse({ chapters: [] });
      }
      if (url === '/api/jobs/snapshot') {
        return jsonResponse({ events: [] });
      }
      throw new Error(`unexpected url ${url}`);
    });
    vi.stubGlobal('fetch', fetchSpy);
    const { default: App } = await import('./App');

    render(<App />);
    fireEvent.change(await screen.findByLabelText('Local folder path'), { target: { value: 'D:\\books\\one' } });
    fireEvent.click(screen.getByRole('button', { name: 'Preview folder' }));

    expect(await screen.findByText('chapter-1.txt')).toBeVisible();
    expect(fetchSpy.mock.calls.some(([url]) => url === '/api/projects/project-1/chapters/import')).toBe(false);

    fireEvent.click(screen.getByRole('button', { name: 'Confirm import mapping' }));

    await waitFor(() => expect(fetchSpy).toHaveBeenCalledWith('/api/projects/project-1/chapters/import', expect.anything()));
    const importCall = fetchSpy.mock.calls.find(([url]) => url === '/api/projects/project-1/chapters/import');
    expect(JSON.parse(String(importCall?.[1]?.body))).toEqual({
      kind: 'PASTE',
      items: [{ ordinal: 1, title: 'Chuong 1', text: 'Chuong 1\nPreview body.' }],
    });
  });

  it('mounts the U08 quality/quote panel on the translation screen once a cloud profile is set', async () => {
    window.history.replaceState(null, '', '/chapters/chapter-1/translation');
    vi.stubGlobal('EventSource', undefined);
    const fetchSpy = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/chapters/chapter-1/translation') {
        return jsonResponse(translationPayload('run-1'));
      }
      if (url === '/api/security/bootstrap') {
        return jsonResponse({ csrfToken: 'token-1' });
      }
      if (url === '/api/chapters/chapter-1/translation/quote' && init?.method === 'POST') {
        return jsonResponse({
          budgetAuthorizationId: 'budget-1',
          stage: 'TRANSLATE',
          totalVnd: 12_000,
          estimateVnd: 10_000,
          contingencyVnd: 2_000,
          expiresAt: new Date(Date.now() + 60_000).toISOString(),
          quoteHash: 'a'.repeat(64),
          warnings: [],
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
    await screen.findByText('Đã tải bản dịch hiện tại');

    // No profile yet -> the per-stage quality panel is not mounted.
    expect(screen.queryByRole('heading', { name: 'Translation quality' })).toBeNull();

    // Entering a cloud profile mounts the U08 panel (Balanced default, per-stage).
    fireEvent.change(screen.getByLabelText('Gemini profile ID'), { target: { value: 'profile-1' } });
    expect(screen.getByRole('heading', { name: 'Translation quality' })).toBeVisible();
    expect(screen.getByLabelText('Chế độ')).toHaveValue('BALANCED');

    // Quality/Maximum are opt-in: choosing them without the opt-in checkbox
    // blocks the quote request entirely (no POST goes out).
    fireEvent.change(screen.getByLabelText('Chế độ'), { target: { value: 'QUALITY' } });
    fireEvent.click(screen.getByRole('button', { name: 'Lấy báo giá' }));
    await waitFor(() =>
      expect(screen.getByText('Cần bật opt-in trước khi lấy báo giá cho Quality/Maximum.')).toBeVisible(),
    );
    expect(fetchSpy.mock.calls.some(([url]) => url === '/api/chapters/chapter-1/translation/quote')).toBe(false);
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
