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

describe('GlossaryManager scope preview (U09 round 2)', () => {
  const hash64 = '1234567890abcdef'.repeat(4);

  const previewResponse = (overrides: Record<string, unknown> = {}) => ({
    revision_preview_sha256: hash64,
    changed: true,
    affectedSegments: [
      { segmentId: 'seg-1', chapterId: 'ch-1', ordinal: 1, excerpt: '林动抬头。' },
      { segmentId: 'seg-2', chapterId: 'ch-2', ordinal: 2, excerpt: '林动笑了。' },
    ],
    affectedCount: 2,
    invalidatedRuns: ['run-1'],
    invalidatedCount: 1,
    warnings: ['ORDINAL_OUT_OF_RANGE'],
    ...overrides,
  });

  async function fillDraft() {
    fireEvent.change(screen.getByLabelText('Thuật ngữ gốc'), { target: { value: '林动' } });
    fireEvent.change(screen.getByLabelText('Bản dịch chuẩn'), { target: { value: 'Lâm Động' } });
    fireEvent.change(screen.getByLabelText('Scope từ chương'), { target: { value: '1' } });
    fireEvent.change(screen.getByLabelText('đến chương'), { target: { value: '999' } });
  }

  it('shows affected scope and warnings before saving without saving', async () => {
    const calls = mockFetch((url, init) => {
      if (init?.method === 'POST' && url.endsWith('/glossary/preview')) {
        return { payload: previewResponse() };
      }
      return { payload: revisionResponse([]) };
    });

    render(<GlossaryManager projectId="project-1" />);
    await waitFor(() => expect(screen.getByText('Chưa có thuật ngữ nào.')).toBeTruthy());

    await fillDraft();
    fireEvent.click(screen.getByRole('button', { name: 'Xem phạm vi' }));

    const panel = await screen.findByTestId('scope-preview');
    expect(panel.textContent).toContain('2 segment');
    expect(panel.textContent).toContain('1 bản dịch sẽ bị');
    expect(panel.textContent).toContain('ORDINAL_OUT_OF_RANGE');
    expect(panel.textContent).toContain('林动抬头。');
    expect(panel.textContent).toContain('chưa lưu');

    const previewCall = calls.find(
      (call) => call.init?.method === 'POST' && call.url.endsWith('/glossary/preview'),
    );
    expect(previewCall?.url).toBe('/api/projects/project-1/glossary/preview');
    expect(JSON.parse(String(previewCall?.init?.body))).toMatchObject({
      source_term: '林动',
      target_term: 'Lâm Động',
      scope_from_ordinal: 1,
      scope_to_ordinal: 999,
    });

    const upsertCalls = calls.filter(
      (call) => call.init?.method === 'POST' && !call.url.endsWith('/glossary/preview'),
    );
    expect(upsertCalls).toHaveLength(0);
  });

  it('still saves normally after a preview', async () => {
    const calls = mockFetch((url, init) => {
      if (init?.method === 'POST' && url.endsWith('/glossary/preview')) {
        return { payload: previewResponse() };
      }
      if (init?.method === 'POST') {
        return {
          payload: revisionResponse([entry()], {
            affected_source_segment_ids: ['seg-1'],
            invalidated: ['run-1'],
          }),
        };
      }
      return { payload: revisionResponse([]) };
    });

    render(<GlossaryManager projectId="project-1" />);
    await waitFor(() => expect(screen.getByText('Chưa có thuật ngữ nào.')).toBeTruthy());

    await fillDraft();
    fireEvent.click(screen.getByRole('button', { name: 'Xem phạm vi' }));
    await screen.findByTestId('scope-preview');

    fireEvent.click(screen.getByRole('button', { name: 'Lưu thuật ngữ' }));

    // The preview panel also exposes role="status"; wait for the save banner.
    await waitFor(() => {
      const saveStatus = screen
        .getAllByRole('status')
        .find((node) => node.textContent?.includes('Đã lưu thuật ngữ'));
      expect(saveStatus?.textContent).toContain('1 segment');
      expect(saveStatus?.textContent).toContain('1 bản dịch bị đánh stale');
    });
    await waitFor(() => expect(screen.queryByTestId('scope-preview')).toBeNull());
    expect(
      calls.filter(
        (call) => call.init?.method === 'POST' && !call.url.endsWith('/glossary/preview'),
      ),
    ).toHaveLength(1);
    expect((screen.getByLabelText('Thuật ngữ gốc') as HTMLInputElement).value).toBe('');
  });

  it('surfaces preview errors with the raw backend code', async () => {
    mockFetch((url, init) =>
      init?.method === 'POST' && url.endsWith('/glossary/preview')
        ? { status: 400, payload: { detail: 'GLOSSARY_SCOPE_INVALID' } }
        : { payload: revisionResponse([]) },
    );

    render(<GlossaryManager projectId="project-1" />);
    await waitFor(() => expect(screen.getByText('Chưa có thuật ngữ nào.')).toBeTruthy());

    await fillDraft();
    fireEvent.click(screen.getByRole('button', { name: 'Xem phạm vi' }));

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('GLOSSARY_SCOPE_INVALID');
    expect(screen.queryByTestId('scope-preview')).toBeNull();
  });
});
