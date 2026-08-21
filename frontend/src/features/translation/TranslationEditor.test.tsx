import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import TranslationEditor from './TranslationEditor';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('TranslationEditor', () => {
  it('saves a revision with the expected run hash and keeps it after refresh', async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/chapters/chapter-1/translation') {
        return jsonResponse({
          run: { id: 'run-1', sha256: 'a'.repeat(64), status: 'REVIEW' },
          segments: [
            {
              id: 'ts-1',
              sourceSegmentId: 'source-1',
              sourceText: '林动 has 42 coins.',
              targetText: 'Lam Dong co 42 dong.',
            },
          ],
          issues: [
            {
              id: 'issue-1',
              category: 'NUMBER_UNIT',
              severity: 'MINOR',
              status: 'OPEN',
              evidence: '42',
              suggestion: 'Kiem tra so.',
              sourceSegmentId: 'source-1',
            },
          ],
        });
      }
      if (url === '/api/chapters/chapter-1/translation/segments/source-1') {
        expect(init?.method).toBe('PATCH');
        expect(JSON.parse(String(init?.body))).toEqual({
          runId: 'run-1',
          targetText: 'Ban sua 42',
          expectedRunHash: 'a'.repeat(64),
        });
        return jsonResponse({
          run: { id: 'run-2', sha256: 'b'.repeat(64), status: 'REVIEW' },
          segments: [
            {
              id: 'ts-2',
              sourceSegmentId: 'source-1',
              sourceText: '林动 has 42 coins.',
              targetText: 'Ban sua 42',
            },
          ],
          issues: [],
        });
      }
      throw new Error(`unexpected url ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<TranslationEditor chapterId="chapter-1" />);

    const editor = await screen.findByLabelText('Ban dich source-1');
    fireEvent.change(editor, { target: { value: 'Ban sua 42' } });
    fireEvent.click(screen.getByRole('button', { name: 'Luu ban sua' }));

    await waitFor(() => expect(screen.getByText('Da luu ban sua')).toBeVisible());
    expect(screen.getByLabelText('Ban dich source-1')).toHaveValue('Ban sua 42');
    expect(screen.getByText('run-2')).toBeVisible();
  });

  it('surfaces stale edit conflicts without overwriting the editor text', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (url === '/api/chapters/chapter-1/translation') {
          return jsonResponse({
            run: { id: 'run-1', sha256: 'a'.repeat(64), status: 'REVIEW' },
            segments: [
              {
                id: 'ts-1',
                sourceSegmentId: 'source-1',
                sourceText: '林动',
                targetText: 'Lam Dong',
              },
            ],
            issues: [],
          });
        }
        return {
          ok: false,
          status: 409,
          json: async () => ({ detail: 'TRANSLATION_RUN_HASH_MISMATCH' }),
        } as Response;
      }),
    );

    render(<TranslationEditor chapterId="chapter-1" />);

    const editor = await screen.findByLabelText('Ban dich source-1');
    fireEvent.change(editor, { target: { value: 'Ban dang sua' } });
    fireEvent.click(screen.getByRole('button', { name: 'Luu ban sua' }));

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('TRANSLATION_RUN_HASH_MISMATCH'));
    expect(screen.getByLabelText('Ban dich source-1')).toHaveValue('Ban dang sua');
  });

  it('filters issues by severity', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse({
          run: { id: 'run-1', sha256: 'a'.repeat(64), status: 'REVIEW' },
          segments: [
            {
              id: 'ts-1',
              sourceSegmentId: 'source-1',
              sourceText: '林动',
              targetText: 'Lam Dong',
            },
          ],
          issues: [
            {
              id: 'major-1',
              category: 'NAME',
              severity: 'MAJOR',
              status: 'OPEN',
              evidence: 'Lam Dong',
              suggestion: 'Dung ten khoa.',
              sourceSegmentId: 'source-1',
            },
            {
              id: 'minor-1',
              category: 'STYLE',
              severity: 'MINOR',
              status: 'OPEN',
              evidence: 'cau dai',
              suggestion: 'Rut gon.',
              sourceSegmentId: 'source-1',
            },
          ],
        }),
      ),
    );

    render(<TranslationEditor chapterId="chapter-1" />);

    expect(await screen.findByText('Dung ten khoa.')).toBeVisible();
    fireEvent.change(screen.getByLabelText('Loc loi'), { target: { value: 'MAJOR' } });

    expect(screen.getByText('Dung ten khoa.')).toBeVisible();
    expect(screen.queryByText('Rut gon.')).not.toBeInTheDocument();
  });
});

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: async () => body,
  } as Response;
}
