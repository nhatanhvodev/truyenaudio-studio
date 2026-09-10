import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import BilingualEditor from './BilingualEditor';

type Call = { url: string; init?: RequestInit };

function jsonResponse(body: unknown, status = 200): Response {
  const text = JSON.stringify(body);
  return { ok: status < 400, status, text: async () => text, json: async () => body } as Response;
}

function mockFetch(handler: (url: string, init?: RequestInit) => Response) {
  const calls: Call[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith('/api/security/bootstrap')) {
        return jsonResponse({ csrfToken: 'test-token' });
      }
      calls.push({ url, init });
      return handler(url, init);
    }),
  );
  return calls;
}

function payload(overrides: Record<string, unknown> = {}) {
  return {
    run: { id: 'run-1', sha256: 'a'.repeat(64), status: 'REVIEW' },
    segments: [
      { id: 'ts-1', sourceSegmentId: 'seg-1', sourceText: '林动 có 42 đồng.', targetText: 'Lam Dong co 42 dong.' },
      { id: 'ts-2', sourceSegmentId: 'seg-2', sourceText: '第二段', targetText: 'Doan hai.' },
    ],
    issues: [],
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('BilingualEditor (U06 round 1)', () => {
  it('renders aligned rows by stable id and keeps the source read-only', async () => {
    mockFetch(() => jsonResponse(payload()));

    render(<BilingualEditor chapterId="chapter-1" />);

    expect(await screen.findByText('Lam Dong co 42 dong.')).toBeTruthy();
    // Only the two target panes are editable; the source is plain text.
    expect(screen.getAllByRole('textbox')).toHaveLength(2);
    const source = screen.getByTestId('source-seg-1');
    expect(source).toHaveAttribute('aria-readonly', 'true');
    expect(within(source).queryByRole('textbox')).toBeNull();
    expect(source.tagName).toBe('P');
    // Rows keep the stable key even after a filter change.
    fireEvent.change(screen.getByLabelText('Lọc QA'), { target: { value: 'CRITICAL' } });
    expect(screen.getByTestId('row-seg-1')).toBeTruthy();
    expect(screen.getByTestId('row-seg-2')).toBeTruthy();
  });

  it('refuses to save while an IME composition is active, then saves after composition ends', async () => {
    const calls = mockFetch((url, init) => {
      if (init?.method === 'PATCH') {
        return jsonResponse(payload());
      }
      return jsonResponse(payload());
    });

    render(<BilingualEditor chapterId="chapter-1" />);
    const editor = await screen.findByLabelText('Bản dịch seg-1');
    fireEvent.change(editor, { target: { value: 'Lam Động' } });

    fireEvent.compositionStart(editor);
    fireEvent.keyDown(editor, { key: 's', ctrlKey: true });
    expect(await screen.findByRole('alert')).toHaveTextContent('IME_COMPOSITION_ACTIVE');
    expect(calls.some((call) => call.init?.method === 'PATCH')).toBe(false);

    fireEvent.click(screen.getByRole('button', { name: 'Lưu đoạn seg-1' }));
    expect(calls.some((call) => call.init?.method === 'PATCH')).toBe(false);

    fireEvent.compositionEnd(editor);
    fireEvent.keyDown(editor, { key: 's', ctrlKey: true });

    await waitFor(() => expect(calls.some((call) => call.init?.method === 'PATCH')).toBe(true));
    const patch = calls.find((call) => call.init?.method === 'PATCH');
    expect(JSON.parse(String(patch?.init?.body))).toEqual({
      runId: 'run-1',
      targetText: 'Lam Động',
      expectedRunHash: 'a'.repeat(64),
    });
  });

  it('saves the edited segment with Ctrl+S using the expected run hash', async () => {
    const calls = mockFetch((url, init) => (init?.method === 'PATCH' ? jsonResponse(payload()) : jsonResponse(payload())));

    render(<BilingualEditor chapterId="chapter-1" />);
    const second = await screen.findByLabelText('Bản dịch seg-2');
    fireEvent.change(second, { target: { value: 'Đoạn hai (đã sửa)' } });
    fireEvent.keyDown(second, { key: 's', ctrlKey: true });

    await waitFor(() => expect(screen.getByText('Đã lưu câu dịch')).toBeVisible());
    expect(calls.find((call) => call.init?.method === 'PATCH')?.url).toBe(
      '/api/chapters/chapter-1/translation/segments/seg-2',
    );
  });

  it('jumps to the segment an issue belongs to and filters the inspector', async () => {
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;
    mockFetch(() =>
      jsonResponse(
        payload({
          issues: [
            {
              id: 'issue-critical',
              category: 'NUMBER_UNIT',
              severity: 'CRITICAL',
              status: 'OPEN',
              evidence: '42',
              suggestion: 'Kiểm tra số 42.',
              sourceSegmentId: 'seg-2',
            },
            {
              id: 'issue-minor',
              category: 'STYLE',
              severity: 'MINOR',
              status: 'OPEN',
              evidence: 'câu dài',
              suggestion: 'Rút gọn câu.',
              sourceSegmentId: 'seg-1',
            },
          ],
        }),
      ),
    );

    render(<BilingualEditor chapterId="chapter-1" />);
    const inspector = await screen.findByLabelText('QA inspector');
    expect(within(inspector).getAllByRole('button')).toHaveLength(2);

    fireEvent.click(within(inspector).getByText(/Kiểm tra số 42/));

    expect(screen.getByTestId('row-seg-2')).toHaveAttribute('data-revealed', 'true');
    expect(screen.getByTestId('row-seg-1')).toHaveAttribute('data-revealed', 'false');
    expect(scrollIntoView).toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText('Lọc QA'), { target: { value: 'MINOR' } });

    expect(within(screen.getByLabelText('QA inspector')).queryByText(/Kiểm tra số 42/)).toBeNull();
    expect(within(screen.getByLabelText('QA inspector')).getByText(/Rút gọn câu/)).toBeVisible();
  });

  it('applies a QA proposal into the draft without touching the source', async () => {
    mockFetch(() =>
      jsonResponse(
        payload({
          issues: [
            {
              id: 'issue-1',
              category: 'NUMBER_UNIT',
              severity: 'MAJOR',
              status: 'OPEN',
              evidence: '42',
              suggestion: 'Lam Động có 42 đồng.',
              sourceSegmentId: 'seg-1',
            },
          ],
        }),
      ),
    );

    render(<BilingualEditor chapterId="chapter-1" />);
    await screen.findByLabelText('Bản dịch seg-1');

    fireEvent.click(screen.getByRole('button', { name: 'Áp dụng đề xuất QA' }));

    expect(screen.getByLabelText('Bản dịch seg-1')).toHaveValue('Lam Động có 42 đồng.');
    expect(screen.getByTestId('source-seg-1')).toHaveTextContent('林动 có 42 đồng.');
  });

  it('blocks approve while a CRITICAL issue is open and allows an explicit override', async () => {
    const calls = mockFetch((url, init) => {
      if (url.endsWith('/approve') && init?.method === 'POST') {
        return jsonResponse({ runId: 'run-1', status: 'APPROVED' });
      }
      return jsonResponse(
        payload({
          issues: [
            {
              id: 'issue-critical',
              category: 'NUMBER_UNIT',
              severity: 'CRITICAL',
              status: 'OPEN',
              evidence: '42',
              suggestion: 'Kiểm tra số 42.',
              sourceSegmentId: 'seg-2',
            },
          ],
        }),
      );
    });
    const onApproved = vi.fn();

    render(<BilingualEditor chapterId="chapter-1" onApproved={onApproved} />);
    const approve = await screen.findByRole('button', { name: 'Phê duyệt' });
    expect(approve).toBeDisabled();
    expect(screen.getByText(/Còn 1 lỗi CRITICAL/)).toBeVisible();
    expect(calls.some((call) => call.url.endsWith('/approve'))).toBe(false);

    fireEvent.click(screen.getByRole('button', { name: /Bỏ qua cảnh báo & phê duyệt/ }));

    await waitFor(() => expect(onApproved).toHaveBeenCalledTimes(1));
    const approveCall = calls.find((call) => call.url.endsWith('/approve'));
    expect(JSON.parse(String(approveCall?.init?.body))).toEqual({
      runId: 'run-1',
      expectedRunHash: 'a'.repeat(64),
      force: true,
    });
  });

  it('keeps the local text and surfaces the code on a revision conflict', async () => {
    mockFetch((url, init) => {
      if (init?.method === 'PATCH') {
        return jsonResponse({ detail: 'TRANSLATION_RUN_HASH_MISMATCH' }, 409);
      }
      return jsonResponse(payload());
    });

    render(<BilingualEditor chapterId="chapter-1" />);
    const editor = await screen.findByLabelText('Bản dịch seg-1');
    fireEvent.change(editor, { target: { value: 'Bản đang sửa' } });
    fireEvent.click(screen.getByRole('button', { name: 'Lưu đoạn seg-1' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('TRANSLATION_RUN_HASH_MISMATCH');
    expect(screen.getByLabelText('Bản dịch seg-1')).toHaveValue('Bản đang sửa');
  });
});
