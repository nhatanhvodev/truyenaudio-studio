import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  GlossaryManager,
  buildEntryPayload,
  parseForbiddenForms,
  validateDraft,
} from './GlossaryManager';

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

const entry = (overrides: Record<string, unknown> = {}) => ({
  id: 'g1',
  source_term: '林动',
  target_term: 'Lâm Động',
  reading: 'lâm động',
  category: 'NAME',
  gender: null,
  addressing_notes: null,
  is_locked: true,
  revision_no: 2,
  description: null,
  forbidden_forms: ['Lam Dong'],
  evidence: null,
  scope_from_ordinal: 1,
  scope_to_ordinal: 3,
  ...overrides,
});

const revisionResponse = (entries: unknown[], extra: Record<string, unknown> = {}) => ({
  revision: { entries, sha256: 'abcdef0123456789'.padEnd(64, '0') },
  affected_source_segment_ids: [],
  invalidated: [],
  ...extra,
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('glossary helpers', () => {
  it('normalizes forbidden forms and rejects contradictions', () => {
    expect(parseForbiddenForms(' Lam Dong , lam dong ,, ')).toEqual(['Lam Dong', 'lam dong']);
    expect(
      validateDraft({
        sourceTerm: '林动',
        targetTerm: 'Lâm Động',
        reading: '',
        category: '',
        gender: '',
        addressingNotes: '',
        isLocked: true,
        description: '',
        forbiddenForms: 'Lâm Động',
        evidence: '',
        scopeFrom: '',
        scopeTo: '',
      }),
    ).toBe('GLOSSARY_CONFLICT_INTERNAL');
  });

  it('builds the payload with scope and forbidden fields', () => {
    const payload = buildEntryPayload({
      sourceTerm: ' 林动 ',
      targetTerm: ' Lâm Động ',
      reading: 'lâm động',
      category: 'NAME',
      gender: '',
      addressingNotes: '',
      isLocked: true,
      description: 'Nhân vật chính',
      forbiddenForms: 'Lam Dong',
      evidence: 'Chương 1',
      scopeFrom: '1',
      scopeTo: '3',
    });
    expect(payload).toMatchObject({
      source_term: '林动',
      target_term: 'Lâm Động',
      is_locked: true,
      forbidden_forms: ['Lam Dong'],
      scope_from_ordinal: 1,
      scope_to_ordinal: 3,
      evidence: 'Chương 1',
    });
  });
});

describe('GlossaryManager (U09 part 2)', () => {
  it('renders locked entries with scope and forbidden forms', async () => {
    mockFetch(() => ({ payload: revisionResponse([entry(), entry({ id: 'g2', is_locked: false, forbidden_forms: [], scope_from_ordinal: null, scope_to_ordinal: null })]) }));

    render(<GlossaryManager projectId="project-1" />);

    await waitFor(() => expect(screen.getByRole('table')).toBeTruthy());
    const table = screen.getByRole('table');
    expect(within(table).getByText('Có')).toBeTruthy();
    expect(within(table).getByText('chương 1–3')).toBeTruthy();
    expect(within(table).getByText('toàn project')).toBeTruthy();
    expect(within(table).getByText('Lam Dong')).toBeTruthy();
  });

  it('validates locally before calling the API', async () => {
    const calls = mockFetch(() => ({ payload: revisionResponse([]) }));

    render(<GlossaryManager projectId="project-1" />);
    await waitFor(() => expect(screen.getByText('Chưa có thuật ngữ nào.')).toBeTruthy());

    fireEvent.click(screen.getByRole('button', { name: 'Lưu thuật ngữ' }));
    await waitFor(() =>
      expect(screen.getByRole('alert').textContent).toContain('GLOSSARY_SOURCE_TERM_REQUIRED'),
    );
    expect(calls.filter((call) => call.init?.method === 'POST')).toHaveLength(0);
  });

  it('posts the entry and reports affected/stale scope after save', async () => {
    const calls = mockFetch((url, init) => {
      if (init?.method === 'POST') {
        return {
          payload: revisionResponse([entry()], {
            affected_source_segment_ids: ['seg-1', 'seg-2'],
            invalidated: ['run-1'],
          }),
        };
      }
      return { payload: revisionResponse([]) };
    });

    render(<GlossaryManager projectId="project-1" />);
    await waitFor(() => expect(screen.getByText('Chưa có thuật ngữ nào.')).toBeTruthy());

    fireEvent.change(screen.getByLabelText('Thuật ngữ gốc'), { target: { value: '林动' } });
    fireEvent.change(screen.getByLabelText('Bản dịch chuẩn'), { target: { value: 'Lâm Động' } });
    fireEvent.click(screen.getByLabelText('Khóa thuật ngữ'));
    fireEvent.change(screen.getByLabelText('Forbidden form (phẩy)'), { target: { value: 'Lam Dong' } });
    fireEvent.change(screen.getByLabelText('Scope từ chương'), { target: { value: '1' } });
    fireEvent.change(screen.getByLabelText('đến chương'), { target: { value: '3' } });
    fireEvent.click(screen.getByRole('button', { name: 'Lưu thuật ngữ' }));

    await waitFor(() => expect(calls.some((call) => call.init?.method === 'POST')).toBe(true));
    const post = calls.find((call) => call.init?.method === 'POST');
    expect(post?.url).toBe('/api/projects/project-1/glossary');
    expect(JSON.parse(String(post?.init?.body))).toMatchObject({
      source_term: '林动',
      target_term: 'Lâm Động',
      is_locked: true,
      forbidden_forms: ['Lam Dong'],
      scope_from_ordinal: 1,
      scope_to_ordinal: 3,
    });

    const status = await screen.findByRole('status');
    expect(status.textContent).toContain('2 segment');
    expect(status.textContent).toContain('1 bản dịch bị đánh stale');
  });

  it('surfaces backend conflict errors as alerts', async () => {
    mockFetch((url, init) =>
      init?.method === 'POST'
        ? { status: 400, payload: { detail: 'GLOSSARY_SCOPE_INVALID' } }
        : { payload: revisionResponse([]) },
    );

    render(<GlossaryManager projectId="project-1" />);
    await waitFor(() => expect(screen.getByText('Chưa có thuật ngữ nào.')).toBeTruthy());

    fireEvent.change(screen.getByLabelText('Thuật ngữ gốc'), { target: { value: '林动' } });
    fireEvent.change(screen.getByLabelText('Bản dịch chuẩn'), { target: { value: 'Lâm Động' } });
    fireEvent.click(screen.getByRole('button', { name: 'Lưu thuật ngữ' }));

    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('GLOSSARY_SCOPE_INVALID'));
  });
});
