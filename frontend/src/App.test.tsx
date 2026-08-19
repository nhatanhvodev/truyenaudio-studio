import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from './App';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('App', () => {
  it('renders the local diagnostics dashboard from loopback-relative APIs', async () => {
    const fetchMock = vi.fn(async (url: string) => {
      if (url === '/api/health/ready') {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: 'ready',
            components: {
              api: { status: 'ok', message: 'live' },
              worker: { status: 'ok', message: 'fresh' },
              sqlite: { status: 'ok', message: 'writable' },
              ffmpeg: { status: 'ok', message: 'available' },
            },
          }),
        };
      }
      if (url === '/api/poc/status') {
        return {
          ok: true,
          status: 200,
          json: async () => ({ status: 'ready', gates: { overall: { passed: true } } }),
        };
      }
      throw new Error(`unexpected url ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    expect(await screen.findByRole('heading', { name: 'Truyện Audio Studio' })).toBeVisible();
    expect(await screen.findByText('API')).toBeVisible();
    expect(screen.getByText('Worker')).toBeVisible();
    expect(screen.getByText('SQLite')).toBeVisible();
    expect(screen.getByText('FFmpeg')).toBeVisible();
    expect(screen.getByText('POC Gate')).toBeVisible();
    expect(screen.getByRole('link', { name: 'Chẩn đoán' })).toHaveAttribute('href', '/diagnostics');
    expect(fetchMock).toHaveBeenCalledWith('/api/health/ready', expect.objectContaining({ cache: 'no-store' }));
    expect(fetchMock).toHaveBeenCalledWith('/api/poc/status', expect.objectContaining({ cache: 'no-store' }));
  });

  it('shows offline and degraded states accessibly', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (url === '/api/health/ready') {
          return {
            ok: false,
            status: 503,
            json: async () => ({
              status: 'not_ready',
              components: {
                api: { status: 'ok', message: 'live' },
                worker: { status: 'stale', message: 'heartbeat qua cu' },
              },
            }),
          };
        }
        throw new TypeError('network offline');
      }),
    );

    render(<App />);

    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('API cần kiểm tra'));
    expect(screen.getByText('Mất kết nối local')).toBeVisible();
  });
});
