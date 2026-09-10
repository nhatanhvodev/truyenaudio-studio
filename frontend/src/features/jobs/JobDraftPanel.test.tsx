import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import JobDraftPanel from './JobDraftPanel';
import type { EventSourceLike } from './useJobDraftStream';

type Listener = (event: MessageEvent) => void;

class StubEventSource implements EventSourceLike {
  static instances: StubEventSource[] = [];
  listeners: Record<string, Listener[]> = {};
  closed = false;
  url: string;
  onerror: ((event: unknown) => void) | null = null;
  onopen: ((event: unknown) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    StubEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: Listener): void {
    this.listeners[type] = [...(this.listeners[type] ?? []), listener];
  }

  close(): void {
    this.closed = true;
  }

  emit(type: string, data: unknown): void {
    for (const listener of this.listeners[type] ?? []) {
      listener({ data: JSON.stringify(data) } as MessageEvent);
    }
  }

  open(): void {
    this.onopen?.({});
  }
}

function jsonResponse(body: unknown, status = 200): Response {
  const text = JSON.stringify(body);
  return { ok: status < 400, status, text: async () => text, json: async () => body } as Response;
}

function mockFetch(snapshot: unknown, status = 200) {
  const calls: string[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      calls.push(url);
      return jsonResponse(snapshot, status);
    }),
  );
  return calls;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  StubEventSource.instances = [];
});

describe('JobDraftPanel (U07 round 1)', () => {
  it('loads the snapshot then appends live draft frames by offset', async () => {
    mockFetch({ offset: 3, text: 'Một', status: 'DRAFT', approvable: false, draftRevision: 1 });
    render(<JobDraftPanel jobId="job-1" eventSourceFactory={(url) => new StubEventSource(url)} />);

    expect(await screen.findByRole('status', { name: '' })).toBeTruthy();
    await waitFor(() => expect(screen.getByLabelText('Nội dung nháp')).toHaveValue('Một'));

    const source = StubEventSource.instances[0];
    expect(source.url).toBe('/api/jobs/job-1/draft/stream?afterOffset=0');
    source.open();
    source.emit('draft', { offset: 6, text: 'Hai' });

    await waitFor(() => expect(screen.getByLabelText('Nội dung nháp')).toHaveValue('MộtHai'));
    expect(screen.getByLabelText('Trạng thái nháp')).toHaveTextContent('Offset 6');
    expect(screen.getByLabelText('Trạng thái nháp')).toHaveTextContent('đang nhận trực tiếp');
  });

  it('ignores duplicated frames and replaces the buffer on a gap event', async () => {
    mockFetch({ offset: 3, text: 'Một', status: 'DRAFT', approvable: false });
    render(<JobDraftPanel jobId="job-2" eventSourceFactory={(url) => new StubEventSource(url)} />);
    await waitFor(() => expect(screen.getByLabelText('Nội dung nháp')).toHaveValue('Một'));

    const source = StubEventSource.instances[0];
    source.emit('draft', { offset: 3, text: 'Một' });
    await waitFor(() => expect(screen.getByLabelText('Nội dung nháp')).toHaveValue('Một'));

    source.emit('draft', { offset: 6, text: 'Hai' });
    await waitFor(() => expect(screen.getByLabelText('Nội dung nháp')).toHaveValue('MộtHai'));

    // A gap tells the client its cursor is unknown: the snapshot wins.
    source.emit('gap', { offset: 9, text: 'MộtHaiBa', resync: true });

    await waitFor(() => expect(screen.getByLabelText('Nội dung nháp')).toHaveValue('MộtHaiBa'));
    expect(screen.getByLabelText('Trạng thái nháp')).toHaveTextContent('Offset 9');
  });

  it('marks the stream as finished on terminal and closes it without losing the text', async () => {
    mockFetch({ offset: 3, text: 'Một', status: 'DRAFT', approvable: false });
    render(<JobDraftPanel jobId="job-3" eventSourceFactory={(url) => new StubEventSource(url)} />);
    await waitFor(() => expect(screen.getByLabelText('Nội dung nháp')).toHaveValue('Một'));

    const source = StubEventSource.instances[0];
    source.emit('terminal', { status: 'FINISHED', approvable: false });

    await waitFor(() => expect(screen.getByText('FINISHED')).toBeVisible());
    expect(source.closed).toBe(true);
    expect(screen.getByLabelText('Nội dung nháp')).toHaveValue('Một');
    // A draft is never approvable in this panel.
    expect(screen.getByText(/Nháp chỉ để xem/)).toBeVisible();
  });

  it('warns when the stream was truncated and resyncs on demand', async () => {
    const calls = mockFetch({ offset: 200, text: 'x'.repeat(10), status: 'DRAFT', truncated: true });
    render(<JobDraftPanel jobId="job-4" eventSourceFactory={(url) => new StubEventSource(url)} />);

    expect(await screen.findByText(/Luồng bị cắt bớt/)).toBeVisible();

    fireEvent.click(screen.getByRole('button', { name: 'Tải lại nháp' }));

    await waitFor(() => expect(calls.some((url) => url.startsWith('/api/jobs/job-4/draft?afterOffset='))).toBe(true));
  });

  it('surfaces snapshot failures and still renders the panel', async () => {
    mockFetch({ detail: 'JOB_NOT_FOUND' }, 404);
    render(<JobDraftPanel jobId="missing" eventSourceFactory={(url) => new StubEventSource(url)} />);

    expect(await screen.findByRole('alert')).toHaveTextContent('JOB_NOT_FOUND');
    expect(screen.getByLabelText('Nội dung nháp')).toHaveValue('');
  });

  it('works without EventSource support (snapshot only)', async () => {
    mockFetch({ offset: 3, text: 'Một', status: 'FINISHED', approvable: false });
    render(<JobDraftPanel jobId="job-5" eventSourceFactory={() => null} />);

    await waitFor(() => expect(screen.getByLabelText('Nội dung nháp')).toHaveValue('Một'));
    expect(screen.getByText('FINISHED')).toBeVisible();
    expect(screen.getByLabelText('Trạng thái nháp')).toHaveTextContent('chưa kết nối');
  });
});
