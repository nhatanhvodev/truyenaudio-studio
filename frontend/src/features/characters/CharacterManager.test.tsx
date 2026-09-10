import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { CharacterManager, parseAddressing, parseList } from './CharacterManager';

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

const character = (overrides: Record<string, unknown> = {}) => ({
  character_id: 'c1',
  revision_id: 'r1',
  revision_no: 1,
  canonical_name: '林动',
  aliases: ['Lâm Động'],
  entity_type: 'PERSON',
  role: null,
  gender: null,
  status: 'CANDIDATE',
  evidence_source_revision_id: null,
  evidence_segment_ids: [],
  ...overrides,
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('character helpers', () => {
  it('parses lists and addressing maps', () => {
    expect(parseList(' Lâm Động , Lam Dong ,, Lâm Động ')).toEqual(['Lâm Động', 'Lam Dong']);
    expect(parseAddressing('call_self=ta\ncall_other=A công tử\nbroken')).toEqual({
      call_self: 'ta',
      call_other: 'A công tử',
    });
  });
});

describe('CharacterManager (U09 part 3)', () => {
  it('shows missing gender as unknown and never guesses', async () => {
    mockFetch(() => ({
      payload: {
        characters: [
          character(),
          character({
            character_id: 'c2',
            canonical_name: '應歡歡',
            aliases: ['Ứng Hoan Hoan'],
            gender: 'FEMALE',
          }),
        ],
      },
    }));

    render(<CharacterManager projectId="project-1" />);

    await waitFor(() => expect(screen.getByRole('table')).toBeTruthy());
    const table = screen.getByRole('table');
    expect(within(table).getByText('chưa rõ')).toBeTruthy();
    expect(within(table).getByText('FEMALE')).toBeTruthy();
    expect(within(table).getByText('Lâm Động')).toBeTruthy();
  });

  it('refuses to approve without evidence and posts evidence when provided', async () => {
    const calls = mockFetch((url, init) => {
      if (init?.method === 'POST') {
        return { payload: { character: character({ status: 'APPROVED' }) } };
      }
      return { payload: { characters: [character()] } };
    });

    render(<CharacterManager projectId="project-1" />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Approve' })).toBeTruthy());

    fireEvent.click(screen.getByRole('button', { name: 'Approve' }));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('CHARACTER_EVIDENCE_REQUIRED'));
    expect(calls.filter((call) => call.init?.method === 'POST')).toHaveLength(0);

    fireEvent.change(screen.getByLabelText('Source revision cho 林动'), {
      target: { value: 'revision-1' },
    });
    fireEvent.change(screen.getByLabelText('Segment ids cho 林动'), { target: { value: 'seg-1,seg-2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }));

    await waitFor(() => expect(calls.some((call) => call.init?.method === 'POST')).toBe(true));
    const post = calls.find((call) => call.init?.method === 'POST');
    expect(post?.url).toBe('/api/projects/project-1/characters/c1/approve');
    expect(JSON.parse(String(post?.init?.body))).toMatchObject({
      evidence_source_revision_id: 'revision-1',
      evidence_segment_ids: ['seg-1', 'seg-2'],
    });
  });

  it('creates a candidate with aliases and no guessed gender', async () => {
    const calls = mockFetch((url, init) => {
      if (init?.method === 'POST') {
        return { payload: { character: character() } };
      }
      return { payload: { characters: [] } };
    });

    render(<CharacterManager projectId="project-1" />);
    await waitFor(() => expect(screen.getByText('Chưa có nhân vật nào.')).toBeTruthy());

    fireEvent.change(screen.getByLabelText('Tên chuẩn'), { target: { value: '林动' } });
    fireEvent.change(screen.getByLabelText('Alias (phẩy)'), { target: { value: 'Lâm Động, Lam Dong' } });
    fireEvent.click(screen.getByRole('button', { name: 'Tạo candidate' }));

    await waitFor(() => expect(calls.some((call) => call.init?.method === 'POST')).toBe(true));
    const body = JSON.parse(String(calls.find((call) => call.init?.method === 'POST')?.init?.body));
    expect(body).toMatchObject({ canonical_name: '林动', entity_type: 'PERSON', gender: null });
    expect(body.aliases).toEqual(['Lâm Động', 'Lam Dong']);
  });

  it('sends directed relationships and lists them for a chapter', async () => {
    const calls = mockFetch((url, init) => {
      if (init?.method === 'POST') {
        return { payload: { relationship: {} } };
      }
      if (url.includes('/relationships?ordinal=5')) {
        return {
          payload: {
            relationships: [
              {
                id: 'rel-1',
                from_character_id: 'c1',
                to_character_id: 'c2',
                from_ordinal: 2,
                to_ordinal: 6,
                addressing: { call_self: 'ta' },
                status: 'ACTIVE',
              },
            ],
          },
        };
      }
      return { payload: { characters: [] } };
    });

    render(<CharacterManager projectId="project-1" />);
    await waitFor(() => expect(screen.getByText('Chưa có nhân vật nào.')).toBeTruthy());

    fireEvent.change(screen.getByLabelText('Từ character id'), { target: { value: 'c1' } });
    fireEvent.change(screen.getByLabelText('Đến character id'), { target: { value: 'c2' } });
    fireEvent.change(screen.getByLabelText('Từ chương'), { target: { value: '2' } });
    fireEvent.change(screen.getByLabelText('Đến chương (trống = mở)'), { target: { value: '6' } });
    fireEvent.change(screen.getByLabelText('Addressing (mỗi dòng key=value)'), {
      target: { value: 'call_self=ta' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Thêm quan hệ' }));

    await waitFor(() => expect(calls.some((call) => call.init?.method === 'POST')).toBe(true));
    const post = calls.find((call) => call.init?.method === 'POST');
    expect(post?.url).toBe('/api/projects/project-1/characters/relationships');
    expect(JSON.parse(String(post?.init?.body))).toMatchObject({
      from_character_id: 'c1',
      to_character_id: 'c2',
      from_ordinal: 2,
      to_ordinal: 6,
      addressing: { call_self: 'ta' },
    });

    fireEvent.change(screen.getByLabelText('Xem quan hệ tại chương'), { target: { value: '5' } });
    const list = await screen.findByLabelText('Quan hệ tại chương đã chọn');
    expect(list.textContent).toContain('c1 → c2');
    expect(list.textContent).toContain('call_self=ta');
  });

  it('surfaces API errors as alerts', async () => {
    mockFetch((url, init) =>
      init?.method === 'POST'
        ? { status: 400, payload: { detail: 'CHARACTER_ALIAS_AMBIGUOUS' } }
        : { payload: { characters: [] } },
    );

    render(<CharacterManager projectId="project-1" />);
    await waitFor(() => expect(screen.getByText('Chưa có nhân vật nào.')).toBeTruthy());

    fireEvent.change(screen.getByLabelText('Tên chuẩn'), { target: { value: '林动' } });
    fireEvent.click(screen.getByRole('button', { name: 'Tạo candidate' }));

    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('CHARACTER_ALIAS_AMBIGUOUS'));
  });
});
