import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { StyleManager } from './StyleManager';

type Call = { url: string; init?: RequestInit };

function mockFetch(handler: (url: string, init?: RequestInit) => { status?: number; payload: unknown }) {
  const calls: Call[] = [];
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/api/security/bootstrap')) {
      return {
        ok: true,
        status: 200,
        text: async () => JSON.stringify({ csrfToken: 'test-token' }),
        json: async () => ({ csrfToken: 'test-token' }),
      };
    }
    calls.push({ url, init });
    const result = handler(url, init);
    const body = JSON.stringify(result.payload);
    return {
      ok: (result.status ?? 200) < 400,
      status: result.status ?? 200,
      text: async () => body,
      json: async () => result.payload,
    };
  });
  vi.stubGlobal('fetch', fn);
  return calls;
}

const preset = {
  key: 'web-novel',
  name: 'Web Novel',
  genre: 'webnovel',
  tone: 'natural',
  user_instruction: 'Dịch tự nhiên cho web novel.',
};

const style = {
  id: 'style-1',
  revision_no: 2,
  name: 'Web Novel',
  genre: 'webnovel',
  tone: 'natural',
  source_language: 'zh-CN',
  target_language: 'vi-VN',
  user_instruction: 'Dịch tự nhiên cho web novel.',
  sha256: 'abcdef0123456789'.padEnd(64, '0'),
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('StyleManager (U09 part 1)', () => {
  it('lists presets and active styles with revision and hash', async () => {
    mockFetch((url) =>
      url.endsWith('/presets')
        ? { payload: { presets: [preset] } }
        : { payload: { styles: [style] } },
    );

    render(<StyleManager projectId="project-1" />);

    await waitFor(() => expect(screen.getByRole('table')).toBeTruthy());
    const table = screen.getByRole('table');
    expect(within(table).getByText('Web Novel')).toBeTruthy();
    expect(within(table).getByText('2')).toBeTruthy();
    expect(within(table).getByText(/abcdef01/)).toBeTruthy();
    expect(screen.getByRole('option', { name: /Web Novel \(webnovel\/natural\)/ })).toBeTruthy();
  });

  it('fills the form from a preset and submits the style payload', async () => {
    const calls = mockFetch((url, init) => {
      if (init?.method === 'POST') {
        return { payload: { style: { ...style, revision_no: 3 }, sha256: style.sha256 } };
      }
      return url.endsWith('/presets')
        ? { payload: { presets: [preset] } }
        : { payload: { styles: [] } };
    });

    render(<StyleManager projectId="project-1" />);
    await waitFor(() => expect(screen.getByText('Chưa có style nào cho project này.')).toBeTruthy());

    fireEvent.change(screen.getByLabelText('Preset có sẵn'), { target: { value: 'web-novel' } });
    expect((screen.getByLabelText('Tên style') as HTMLInputElement).value).toBe('Web Novel');
    expect((screen.getByLabelText(/Custom instruction/) as HTMLTextAreaElement).value).toContain(
      'Dịch tự nhiên',
    );

    fireEvent.click(screen.getByRole('button', { name: 'Lưu style' }));

    await waitFor(() => expect(calls.some((call) => call.init?.method === 'POST')).toBe(true));
    const post = calls.find((call) => call.init?.method === 'POST');
    expect(post?.url).toBe('/api/projects/project-1/styles');
    const body = JSON.parse(String(post?.init?.body));
    expect(body).toMatchObject({
      name: 'Web Novel',
      genre: 'webnovel',
      tone: 'natural',
      source_language: 'zh-CN',
      target_language: 'vi-VN',
    });
    await waitFor(() => expect(screen.getByRole('status').textContent).toContain('revision 3'));
  });

  it('requires a name and surfaces API errors as alerts', async () => {
    let failNext = false;
    mockFetch((url, init) => {
      if (init?.method === 'POST') {
        if (failNext) {
          return { status: 400, payload: { detail: 'STYLE_NAME_REQUIRED' } };
        }
        return { payload: { style, sha256: style.sha256 } };
      }
      return url.endsWith('/presets')
        ? { payload: { presets: [] } }
        : { payload: { styles: [] } };
    });

    render(<StyleManager projectId="project-1" />);
    await waitFor(() => expect(screen.getByText('Chưa có style nào cho project này.')).toBeTruthy());

    fireEvent.click(screen.getByRole('button', { name: 'Lưu style' }));
    await waitFor(() => expect(screen.getAllByRole('alert')[0].textContent).toContain('Cần đặt tên style'));

    fireEvent.change(screen.getByLabelText('Tên style'), { target: { value: 'Broken' } });
    failNext = true;
    fireEvent.click(screen.getByRole('button', { name: 'Lưu style' }));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('STYLE_NAME_REQUIRED'));
  });

  it('reports load failures', async () => {
    mockFetch(() => ({ status: 500, payload: {} }));

    render(<StyleManager projectId="project-1" />);

    await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy());
  });
});
