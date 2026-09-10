import { afterEach, describe, expect, it, vi } from 'vitest';

import { apiBlob, apiForm, apiJson } from './api';

type Call = { url: string; init?: RequestInit };

function stubFetch(payload: unknown = {}, status = 200): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      // The CSRF bootstrap is fetched first and expects { csrfToken }.
      const body = url === '/api/security/bootstrap' ? { csrfToken: 'test-token' } : payload;
      const text = JSON.stringify(body);
      return {
        ok: status < 400,
        status,
        text: async () => text,
        json: async () => body,
        blob: async () => new Blob([text]),
      } as Response;
    }),
  );
  return calls;
}

function headerOf(call: Call | undefined, name: string): string | null {
  return call ? new Headers(call.init?.headers).get(name) : null;
}

/** The CSRF bootstrap is fetched first, so address the real request by URL. */
function callTo(calls: Call[], url: string): Call | undefined {
  return calls.find((call) => call.url === url);
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('api helper request encoding', () => {
  it('sets application/json when the caller passes an object body', async () => {
    const calls = stubFetch({ ok: true });

    await apiJson('/api/thing', { method: 'POST', body: { a: 1 } });

    const post = callTo(calls, '/api/thing');
    expect(headerOf(post, 'Content-Type')).toBe('application/json');
    expect(post?.init?.body).toBe(JSON.stringify({ a: 1 }));
  });

  it('keeps application/json when the caller already stringified the body', async () => {
    // Regression: call sites pass JSON.stringify(...) (a string). The helper
    // used to skip the header for strings, so the browser sent text/plain and
    // FastAPI rejected every save with 422 INVALID_REQUEST.
    const calls = stubFetch({ ok: true });

    await apiJson('/api/thing', { method: 'POST', body: JSON.stringify({ secret: 'x' }) });

    const post = callTo(calls, '/api/thing');
    expect(headerOf(post, 'Content-Type')).toBe('application/json');
    expect(post?.init?.body).toBe(JSON.stringify({ secret: 'x' }));
  });

  it('never overrides an explicit caller Content-Type', async () => {
    const calls = stubFetch({ ok: true });

    await apiJson('/api/thing', {
      method: 'POST',
      headers: { 'Content-Type': 'application/merge-patch+json' },
      body: JSON.stringify({ a: 1 }),
    });

    expect(headerOf(callTo(calls, '/api/thing'), 'Content-Type')).toBe('application/merge-patch+json');
  });

  it('leaves FormData bodies to the browser (multipart boundary)', async () => {
    const calls = stubFetch({ ok: true });
    const form = new FormData();
    form.set('kind', 'PASTE');

    await apiForm('/api/upload', form);

    const upload = callTo(calls, '/api/upload');
    expect(headerOf(upload, 'Content-Type')).toBeNull();
    expect(upload?.init?.body).toBe(form);
  });

  it('applies the same rule to apiBlob', async () => {
    const calls = stubFetch({ ok: true });

    await apiBlob('/api/blob', { method: 'POST', body: JSON.stringify({ a: 1 }) });

    expect(headerOf(callTo(calls, '/api/blob'), 'Content-Type')).toBe('application/json');
  });

  it('adds the CSRF token to state-changing requests only', async () => {
    const calls = stubFetch({ ok: true });

    // The token is cached for the module lifetime, so a state-changing call
    // always carries it while a read does not.
    await apiJson('/api/thing', { method: 'POST', body: JSON.stringify({ a: 1 }) });
    await apiJson('/api/read-only');

    expect(headerOf(callTo(calls, '/api/thing'), 'X-CSRF-Token')).toBe('test-token');
    expect(headerOf(callTo(calls, '/api/read-only'), 'X-CSRF-Token')).toBeNull();
  });
});
