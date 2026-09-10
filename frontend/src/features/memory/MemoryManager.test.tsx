import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { MemoryManager } from './MemoryManager';

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

const candidate = (overrides: Record<string, unknown> = {}) => ({
  id: 'm1',
  entity_key: 'lin-dong',
  entity_type: 'character',
  summary: '林动 đang che giấu thân phận.',
  valid_from_ordinal: 5,
  valid_to_ordinal: null,
  revision_no: 1,
  status: 'CANDIDATE',
  source_run_id: null,
  evidence_segment_ids: [],
  ...overrides,
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('MemoryManager (U09 part 4)', () => {
  it('lists candidates with validity range', async () => {
    mockFetch((url) =>
      url.includes('/candidates')
        ? { payload: { candidates: [candidate(), candidate({ id: 'm2', entity_key: 'fact-1', valid_to_ordinal: 8 })] } }
        : { payload: { entries: [], sha256: '' } },
    );

    render(<MemoryManager projectId="project-1" />);

    await waitFor(() => expect(screen.getByRole('table')).toBeTruthy());
    expect(screen.getByText('từ chương 5+')).toBeTruthy();
    expect(screen.getByText('từ chương 5–8')).toBeTruthy();
  });

  it('requires evidence before approving and posts run + segments', async () => {
    const calls = mockFetch((url, init) => {
      if (init?.method === 'POST' && url.includes('/approve')) {
        return { payload: { entry: candidate({ status: 'APPROVED' }) } };
      }
      if (init?.method === 'POST') {
        return { payload: { entry: candidate() } };
      }
      return { payload: { candidates: [candidate()] } };
    });

    render(<MemoryManager projectId="project-1" />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Duyệt' })).toBeTruthy());

    fireEvent.click(screen.getByRole('button', { name: 'Duyệt' }));
    await waitFor(() =>
      expect(screen.getByRole('alert').textContent).toContain('MEMORY_EVIDENCE_SEGMENTS_REQUIRED'),
    );
    expect(calls.filter((call) => call.url.includes('/approve'))).toHaveLength(0);

    fireEvent.change(screen.getByLabelText('Run id cho lin-dong'), { target: { value: 'run-1' } });
    fireEvent.change(screen.getByLabelText('Segment ids cho lin-dong'), { target: { value: 'seg-1, seg-2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Duyệt' }));

    await waitFor(() =>
      expect(calls.some((call) => call.url.endsWith('/api/projects/project-1/memory/m1/approve'))).toBe(true),
    );
    const approve = calls.find((call) => call.url.includes('/approve'));
    expect(JSON.parse(String(approve?.init?.body))).toMatchObject({
      source_run_id: 'run-1',
      evidence_segment_ids: ['seg-1', 'seg-2'],
    });
  });

  it('rejects a candidate and creates a new one with ordinal scope', async () => {
    const calls = mockFetch((url, init) => {
      if (init?.method === 'POST' && url.endsWith('/reject')) {
        return { payload: { entry: candidate({ status: 'REJECTED' }) } };
      }
      if (init?.method === 'POST') {
        return { payload: { entry: candidate() } };
      }
      return { payload: { candidates: [candidate()] } };
    });

    render(<MemoryManager projectId="project-1" />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Loại' })).toBeTruthy());

    fireEvent.click(screen.getByRole('button', { name: 'Loại' }));
    await waitFor(() => expect(calls.some((call) => call.url.endsWith('/reject'))).toBe(true));

    fireEvent.change(screen.getByLabelText('Entity key'), { target: { value: 'fact-2' } });
    fireEvent.change(screen.getByLabelText('Summary'), { target: { value: 'Sự thật mới.' } });
    fireEvent.change(screen.getByLabelText('Hiệu lực từ chương'), { target: { value: '3' } });
    fireEvent.change(screen.getByLabelText('đến chương'), { target: { value: '7' } });
    fireEvent.click(screen.getByRole('button', { name: 'Tạo candidate' }));

    await waitFor(() => expect(calls.some((call) => call.url.endsWith('/memory/candidates'))).toBe(true));
    const create = calls.find((call) => call.url.endsWith('/memory/candidates') && call.init?.method === 'POST');
    expect(JSON.parse(String(create?.init?.body))).toMatchObject({
      entity_key: 'fact-2',
      summary: 'Sự thật mới.',
      valid_from_ordinal: 3,
      valid_to_ordinal: 7,
    });
  });

  it('shows only approved context for the requested chapter with its hash', async () => {
    const calls = mockFetch((url) =>
      url.includes('/memory/context')
        ? {
            payload: {
              entries: [candidate({ status: 'APPROVED', summary: 'Chương 5: 林动 gặp Tiểu Điêu.' })],
              sha256: 'abcdef0123456789'.padEnd(64, '0'),
            },
          }
        : { payload: { candidates: [] } },
    );

    render(<MemoryManager projectId="project-1" />);
    await waitFor(() => expect(screen.getByText('Không có candidate nào.')).toBeTruthy());

    fireEvent.change(screen.getByLabelText('Xem context tại chương'), { target: { value: '6' } });

    const list = await screen.findByLabelText('Context đã duyệt');
    expect(list.textContent).toContain('Chương 5: 林动 gặp Tiểu Điêu.');
    expect(screen.getByText(/abcdef012345/)).toBeTruthy();
    expect(calls.some((call) => call.url.includes('ordinal=6'))).toBe(true);
  });

  it('surfaces approval errors as alerts', async () => {
    mockFetch((url) =>
      url.includes('/approve')
        ? { status: 400, payload: { detail: 'MEMORY_EVIDENCE_RUN_NOT_APPROVED' } }
        : { payload: { candidates: [candidate()] } },
    );

    render(<MemoryManager projectId="project-1" />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Duyệt' })).toBeTruthy());

    fireEvent.change(screen.getByLabelText('Run id cho lin-dong'), { target: { value: 'run-x' } });
    fireEvent.change(screen.getByLabelText('Segment ids cho lin-dong'), { target: { value: 'seg-9' } });
    fireEvent.click(screen.getByRole('button', { name: 'Duyệt' }));

    await waitFor(() =>
      expect(screen.getByRole('alert').textContent).toContain('MEMORY_EVIDENCE_RUN_NOT_APPROVED'),
    );
  });
});
