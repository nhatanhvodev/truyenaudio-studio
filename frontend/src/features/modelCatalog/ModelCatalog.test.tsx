import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ModelCatalog, buildModelsQuery } from './ModelCatalog';

const model = (overrides: Record<string, unknown> = {}) => ({
  providerId: 'gemini',
  modelId: 'gemini-2.5-flash',
  apiKind: 'chat',
  contextTokens: 1048576,
  languages: ['zh-CN', 'vi-VN'],
  availability: 'available',
  pricing: { class: 'free', currency: 'USD' },
  sourceUrl: 'https://example.test/gemini',
  fetchedAt: '2026-09-09T00:00:00+00:00',
  expiresAt: '2026-09-10T00:00:00+00:00',
  ...overrides,
});

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn());
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function mockFetchOnce(payload: unknown, ok = true) {
  (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
    ok,
    status: ok ? 200 : 500,
    text: async () => JSON.stringify(payload),
  });
}

describe('buildModelsQuery', () => {
  it('sends the server maximum page limit and omits default filters', () => {
    expect(buildModelsQuery({ pricing: 'all', contextMin: '', benchmarked: false })).toBe('limit=100');
  });

  it('includes filters and cursor when set', () => {
    const query = buildModelsQuery({ pricing: 'paid', contextMin: '32000', benchmarked: true }, 'cur-1');
    expect(query).toContain('limit=100');
    expect(query).toContain('pricing=paid');
    expect(query).toContain('contextMin=32000');
    expect(query).toContain('benchmarked=true');
    expect(query).toContain('cursor=cur-1');
  });
});

describe('ModelCatalog', () => {
  it('renders models with provenance and labels unknown price honestly', async () => {
    mockFetchOnce({
      items: [model(), model({ modelId: 'mystery', pricing: { class: 'unknown' }, availability: 'unknown' })],
      stale: true,
    });

    render(<ModelCatalog />);

    await waitFor(() => expect(screen.getAllByRole('row')).toHaveLength(3));
    const table = screen.getByRole('table');
    expect(within(table).getByText('Miễn phí')).toBeTruthy();
    expect(within(table).getByText('Chưa rõ giá')).toBeTruthy();
    expect(within(table).getByText('Chưa xác minh')).toBeTruthy();
    expect(screen.getByRole('status').textContent).toContain('có thể đã cũ');
    expect(screen.getAllByRole('link', { name: 'gemini' })[0].getAttribute('href')).toBe(
      'https://example.test/gemini',
    );
  });

  it('appends the next page and stops when the cursor is exhausted', async () => {
    mockFetchOnce({ items: [model()], nextCursor: 'cur-2' });
    mockFetchOnce({ items: [model({ providerId: 'qwen', modelId: 'qwen-mt-flash' })], nextCursor: null });

    render(<ModelCatalog />);
    await waitFor(() => expect(screen.getAllByRole('row')).toHaveLength(2));

    fireEvent.click(screen.getByRole('button', { name: 'Tải thêm' }));

    await waitFor(() => expect(screen.getAllByRole('row')).toHaveLength(3));
    const table = screen.getByRole('table');
    expect(within(table).getByText('qwen-mt-flash')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Hết danh sách' })).toBeDisabled();
  });

  it('keeps unknown price as not-free when only class is missing', async () => {
    mockFetchOnce({ items: [model({ pricing: {} })], stale: false });

    render(<ModelCatalog />);

    await waitFor(() => expect(within(screen.getByRole('table')).getByText('Chưa rõ giá')).toBeTruthy());
    expect(within(screen.getByRole('table')).queryByText('Miễn phí')).toBeNull();
  });

  it('surfaces a load error as an alert', async () => {
    mockFetchOnce({}, false);

    render(<ModelCatalog />);

    await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy());
  });
});
