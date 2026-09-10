import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ProfileEditor } from './ProfileEditor';

const profile = (overrides: Record<string, unknown> = {}) => ({
  id: 'profile-1',
  providerKind: 'TRANSLATOR',
  adapterName: 'qwen-mt',
  displayName: 'Qwen MT',
  model: 'qwen-mt-flash',
  region: 'frankfurt',
  revision: 1,
  secretConfigured: true,
  config: {},
  enabled: true,
  status: 'ready',
  ...overrides,
});

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

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('ProfileEditor (U08 part 2)', () => {
  it('shows credential state without exposing any secret', async () => {
    mockFetch(() => ({
      payload: {
        profiles: [
          profile(),
          profile({ id: 'p2', displayName: 'Gemini MT', secretConfigured: false, status: 'invalid' }),
        ],
      },
    }));

    render(<ProfileEditor />);

    await waitFor(() => expect(screen.getAllByRole('row')).toHaveLength(3));
    expect(screen.getByText('Đã cấu hình')).toBeTruthy();
    expect(screen.getByText('Chưa có')).toBeTruthy();
    const secretInput = screen.getByLabelText('Credential cho Qwen MT') as HTMLInputElement;
    expect(secretInput.value).toBe('');
    expect(secretInput.type).toBe('password');
  });

  it('sends the credential once, clears the field and never persists it locally', async () => {
    const calls = mockFetch((url, init) => {
      if (init?.method === 'PUT') {
        return { payload: { profile: profile() } };
      }
      return { payload: { profiles: [profile()] } };
    });

    render(<ProfileEditor />);
    await waitFor(() => expect(screen.getByLabelText('Credential cho Qwen MT')).toBeTruthy());

    const input = screen.getByLabelText('Credential cho Qwen MT') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'super-secret-value' } });
    fireEvent.click(screen.getByRole('button', { name: 'Lưu key' }));

    await waitFor(() => expect(calls.some((call) => call.init?.method === 'PUT')).toBe(true));
    const put = calls.find((call) => call.init?.method === 'PUT');
    expect(put?.url).toContain('/api/cloud-profiles/profile-1/credential');
    expect(String(put?.init?.body)).toContain('super-secret-value');
    await waitFor(() =>
      expect((screen.getByLabelText('Credential cho Qwen MT') as HTMLInputElement).value).toBe(''),
    );
    expect(JSON.stringify(window.localStorage)).not.toContain('super-secret-value');
    expect(Object.keys(window.localStorage)).toHaveLength(0);
  });

  it('never sends an empty credential', async () => {
    const calls = mockFetch(() => ({ payload: { profiles: [profile()] } }));

    render(<ProfileEditor />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Lưu key' })).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: 'Lưu key' }));

    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('Cần nhập credential'));
    expect(calls.filter((call) => call.init?.method === 'PUT')).toHaveLength(0);
  });

  it('reports credential validation status and deletes the credential on request', async () => {
    const calls = mockFetch((url, init) => {
      if (url.endsWith('/validate')) {
        return { payload: { status: 'ready', detail: 'credential usable' } };
      }
      if (init?.method === 'DELETE') {
        return { payload: { profile: profile({ secretConfigured: false }) } };
      }
      return { payload: { profiles: [profile()] } };
    });

    render(<ProfileEditor />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Kiểm tra' })).toBeTruthy());

    fireEvent.click(screen.getByRole('button', { name: 'Kiểm tra' }));
    await waitFor(() => expect(screen.getByRole('status').textContent).toContain('ready'));
    expect(calls.some((call) => call.url.endsWith('/api/cloud-profiles/profile-1/validate'))).toBe(true);

    fireEvent.click(screen.getByRole('button', { name: 'Xoá key' }));
    await waitFor(() => expect(calls.some((call) => call.init?.method === 'DELETE')).toBe(true));
    await waitFor(() => expect(screen.getByRole('status').textContent).toContain('Đã xoá credential'));
  });

  it('creates a profile with the drafted values and surfaces API errors', async () => {
    let failNext = false;
    const calls = mockFetch((url, init) => {
      if (init?.method === 'POST' && url.endsWith('/api/cloud-profiles')) {
        if (failNext) {
          return { status: 400, payload: { detail: 'PROVIDER_PROFILE_KIND_MISMATCH' } };
        }
        return { status: 201, payload: { profile: profile({ displayName: 'Gemini MT' }) } };
      }
      return { payload: { profiles: [] } };
    });

    render(<ProfileEditor />);
    await waitFor(() => expect(screen.getByText('Chưa có profile nào.')).toBeTruthy());

    fireEvent.change(screen.getByLabelText('Tên hiển thị'), { target: { value: 'Gemini MT' } });
    fireEvent.click(screen.getByRole('button', { name: 'Tạo profile' }));

    await waitFor(() => expect(calls.some((call) => call.init?.method === 'POST')).toBe(true));
    const post = calls.find((call) => call.init?.method === 'POST');
    expect(String(post?.init?.body)).toContain('Gemini MT');

    failNext = true;
    fireEvent.change(screen.getByLabelText('Tên hiển thị'), { target: { value: 'Broken' } });
    fireEvent.click(screen.getByRole('button', { name: 'Tạo profile' }));
    await waitFor(() =>
      expect(screen.getByRole('alert').textContent).toContain('PROVIDER_PROFILE_KIND_MISMATCH'),
    );
  });
});
