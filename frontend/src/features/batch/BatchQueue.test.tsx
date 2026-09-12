import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { BatchQueue } from './BatchQueue';
import {
  JobEventStore,
  MAX_EVENTS,
  type JobEvent,
  type StreamLike,
} from '../jobs/jobStore';

type Listener = (message: MessageEvent) => void;

class StubStream implements StreamLike {
  static instances: StubStream[] = [];
  listeners: Record<string, Listener[]> = {};
  closed = false;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(public readonly url: string) {
    StubStream.instances.push(this);
  }

  addEventListener(name: string, listener: Listener): void {
    this.listeners[name] = [...(this.listeners[name] ?? []), listener];
  }

  close(): void {
    this.closed = true;
  }

  emitJob(event: JobEvent): void {
    for (const listener of this.listeners.job ?? []) {
      listener({ data: JSON.stringify(event) } as MessageEvent);
    }
  }
}

function event(
  sequenceId: number,
  jobId: string,
  status: string,
  errorCode: string | null = null,
  current = 1,
  total = 4,
): JobEvent {
  return { sequenceId, jobId, status, current, total, errorCode, kind: 'TRANSLATE' };
}

function makeStore(events: JobEvent[] = []): JobEventStore {
  return new JobEventStore({
    fetchSnapshot: async () => events,
    openStream: (url) => new StubStream(url),
    schedule: () => 0,
    cancelScheduled: () => undefined,
  });
}

function jsonResponse(body: unknown, status = 200): Response {
  const text = JSON.stringify(body);
  return { ok: status < 400, status, text: async () => text, json: async () => body } as unknown as Response;
}

function chapter(id: string, ordinal: number) {
  return {
    id,
    ordinal,
    sourceTitle: `Chapter ${ordinal}`,
    translatedTitle: null,
    state: 'IMPORTED',
    progress: { current: 0, total: 0 },
    cost: { estimatedVnd: null, actualVnd: null },
    issues: 0,
    hashes: { sourceSha256: `hash-${ordinal}`, translationSha256: null },
  };
}

type Call = { url: string; init?: RequestInit };

function mockFetch(options: {
  chaptersNextCursor?: string | null;
  batchJobIds?: string[];
  /** Keep returning a cursor on later pages so filtered paging can be exercised. */
  cursorOnEveryCall?: boolean;
} = {}) {
  const calls: Call[] = [];
  let chapterCall = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      if (url.startsWith('/api/security/bootstrap')) {
        return jsonResponse({ csrfToken: 'csrf-token' });
      }
      if (url.includes('/chapters?')) {
        chapterCall += 1;
        const cursor = options.chaptersNextCursor ?? null;
        if (chapterCall >= 2) {
          return jsonResponse({
            items: [chapter('ch-2', 2)],
            nextCursor: options.cursorOnEveryCall ? cursor : null,
            total: 2,
          });
        }
        return jsonResponse({ items: [chapter('ch-1', 1)], nextCursor: cursor, total: 1 });
      }
      if (url === '/api/batches') {
        const jobIds = options.batchJobIds ?? ['job-1', 'job-2'];
        return jsonResponse({
          batchId: 'batch1234567890',
          total: jobIds.length,
          queued: jobIds.length,
          running: 0,
          succeeded: 0,
          failed: 0,
          canceled: 0,
          blocked: 0,
          jobIds,
        });
      }
      if (url.startsWith('/api/jobs/snapshot')) {
        return jsonResponse({ events: [] });
      }
      return jsonResponse({});
    }),
  );
  return calls;
}

async function fillGuardsAndQueue() {
  // The chapter checkbox is a precondition: queueTranslation() returns early with 0 selected.
  await screen.findByText('Chapter 1');
  const checkbox = screen.getByRole('checkbox') as HTMLInputElement;
  if (!checkbox.checked) {
    fireEvent.click(checkbox);
    await waitFor(() => expect(screen.getByText('1/50 selected')).toBeVisible());
  }
  fireEvent.change(screen.getByLabelText('Provider profile ID'), { target: { value: 'prof-1' } });
  fireEvent.change(screen.getByLabelText('Cloud consent ID'), { target: { value: 'consent-1' } });
  fireEvent.change(screen.getByLabelText('Batch operation ID'), { target: { value: 'quote-1' } });
  fireEvent.change(screen.getByLabelText('Budget authorization ID'), { target: { value: 'budget-1' } });
  fireEvent.change(screen.getByLabelText('Estimated input tokens'), { target: { value: '120' } });
  fireEvent.click(screen.getByRole('button', { name: 'Queue translate' }));
  await screen.findByText(/Queued \d+ jobs/);
}

/**
 * The stream the queue opens - waited for, never assumed.
 *
 * `fillGuardsAndQueue` drives the user actions that LEAD to the stream being
 * constructed, but it returns as soon as the "Queued N jobs" text appears. Those
 * two are not ordered: under parallel CPU load the text can be on screen before
 * the stream exists, and `StubStream.instances[0]` then hands back `undefined`.
 * That surfaces as `TypeError: Cannot read properties of undefined (reading
 * 'emitJob')`, which points at the emit rather than at the missing wait.
 *
 * Every caller goes through here so one place owns the wait. Four tests used to
 * call `fillGuardsAndQueue` directly and index the array themselves, which is
 * exactly how they lost the wait that `enqueueAndGetStream` already had.
 */
async function queuedStream(): Promise<StubStream> {
  await fillGuardsAndQueue();
  await waitFor(() => expect(StubStream.instances).toHaveLength(1));
  return StubStream.instances[0];
}

async function enqueueAndGetStream(props: { store: JobEventStore; batchJobIds?: string[] } = { store: makeStore() }) {
  mockFetch({ batchJobIds: props.batchJobIds });
  const view = render(<BatchQueue projectId="p1" store={props.store} />);
  return { view, stream: await queuedStream() };
}

function postCalls(calls: Call[], suffix: string) {
  return calls.filter((call) => call.url.endsWith(suffix) && (call.init?.method ?? 'GET') === 'POST');
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  StubStream.instances = [];
});

describe('BatchQueue (U07 round 3 - theo dõi batch)', () => {
  it('chỉ mở theo dõi sau khi enqueue và hiển thị bảng với đúng số job qua store dùng chung', async () => {
    const calls = mockFetch({ batchJobIds: ['job-1', 'job-2', 'job-3'] });
    const store = makeStore();
    render(<BatchQueue projectId="p1" store={store} />);

    await screen.findByText('Chapter 1');
    // Trước khi có batch: KHÔNG đăng ký store (không mở stream, không đọc snapshot).
    expect(calls.some((call) => call.url.startsWith('/api/jobs/snapshot'))).toBe(false);
    expect(StubStream.instances).toHaveLength(0);

    await fillGuardsAndQueue();
    await waitFor(() => expect(StubStream.instances).toHaveLength(1));
    expect(StubStream.instances[0].url).toBe('/api/events');

    expect(screen.getByText('Tiến độ batch')).toBeVisible();
    expect(screen.getAllByRole('row')).toHaveLength(4); // header + 3 job
    expect(screen.getAllByText('Chưa có sự kiện')).toHaveLength(3);
    // Đúng một subscription trên store dùng chung - không mở connection thứ hai.
    expect(store.subscriberCount).toBe(1);
  });

  it('event RUNNING/SUCCEEDED cập nhật đúng dòng của job', async () => {
    await enqueueAndGetStream();
    const stream = StubStream.instances[0];

    act(() => stream.emitJob(event(1, 'job-1', 'RUNNING')));
    expect(await screen.findByText('Đang chạy')).toBeVisible();
    expect(screen.getByText('1/4')).toBeVisible();

    act(() => stream.emitJob(event(2, 'job-1', 'SUCCEEDED')));
    expect(await screen.findByText('Hoàn tất')).toBeVisible();

    // job-2 chưa có event: vẫn chờ, batch chưa hoàn tất.
    expect(screen.getByText('Chưa có sự kiện')).toBeVisible();
    expect(screen.queryByText('Kết quả batch')).not.toBeInTheDocument();
  });

  it('FAILED ⇒ alert tổng hợp, nút Thử lại chỉ cho job retryable, non-retryable được đánh dấu', async () => {
    await enqueueAndGetStream({ store: makeStore(), batchJobIds: ['job-1', 'job-2', 'job-3'] });
    const stream = StubStream.instances[0];

    act(() => {
      stream.emitJob(event(1, 'job-1', 'FAILED', 'HTTP_500'));
      stream.emitJob(event(2, 'job-2', 'FAILED', 'HTTP_400'));
      stream.emitJob(event(3, 'job-3', 'FAILED', 'HTTP_500'));
    });

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('3/3 job thất bại');
    expect(alert).toHaveTextContent('HTTP_400');

    // Chỉ 2 job retryable (HTTP_400 không bao giờ được mời thử lại).
    expect(screen.getAllByRole('button', { name: 'Thử lại' })).toHaveLength(2);
    expect(screen.getByRole('button', { name: 'Thử lại tất cả lỗi retryable' })).toBeVisible();
    // Job non-retryable được đánh dấu rõ.
    expect(screen.getAllByText(/không thể tự thử lại/)).toHaveLength(2); // dòng + alert
  });

  it('retry POST đúng endpoint và "thử lại tất cả" bỏ qua job non-retryable', async () => {
    const calls = mockFetch({ batchJobIds: ['job-1', 'job-2', 'job-3'] });
    render(<BatchQueue projectId="p1" store={makeStore()} />);
    const stream = await queuedStream();

    act(() => {
      stream.emitJob(event(1, 'job-1', 'FAILED', 'HTTP_500'));
      stream.emitJob(event(2, 'job-2', 'FAILED', 'HTTP_400'));
      stream.emitJob(event(3, 'job-3', 'FAILED', 'HTTP_500'));
    });
    await screen.findByRole('alert');

    fireEvent.click(screen.getAllByRole('button', { name: 'Thử lại' })[0]);
    await waitFor(() => expect(postCalls(calls, '/api/jobs/job-1/retry')).toHaveLength(1));

    fireEvent.click(screen.getByRole('button', { name: 'Thử lại tất cả lỗi retryable' }));
    await waitFor(() => expect(postCalls(calls, '/api/jobs/job-3/retry')).toHaveLength(1));

    const retriedUrls = calls.filter((call) => call.url.endsWith('/retry')).map((call) => call.url);
    expect(retriedUrls).toEqual(['/api/jobs/job-1/retry', '/api/jobs/job-1/retry', '/api/jobs/job-3/retry']);
    // HTTP_400 (non-retryable) không bao giờ được tự retry.
    expect(retriedUrls.some((url) => url.includes('job-2'))).toBe(false);
  });

  it('cancel gọi đúng endpoint, nhãn CANCEL_REQUESTED, ẩn nút hủy khi job kết thúc', async () => {
    const calls = mockFetch({ batchJobIds: ['job-1', 'job-2'] });
    render(<BatchQueue projectId="p1" store={makeStore()} />);
    const stream = await queuedStream();

    act(() => stream.emitJob(event(1, 'job-1', 'RUNNING')));
    fireEvent.click(await screen.findByRole('button', { name: 'Hủy' }));
    await waitFor(() => expect(postCalls(calls, '/api/jobs/job-1/cancel')).toHaveLength(1));

    act(() => stream.emitJob(event(2, 'job-1', 'CANCEL_REQUESTED')));
    expect(await screen.findByText('Đang hủy…')).toBeVisible();
    // job-1 đang hủy không còn nút hủy; job-2 chưa có sự kiện cũng không có.
    expect(screen.queryByRole('button', { name: 'Hủy' })).not.toBeInTheDocument();

    act(() => {
      stream.emitJob(event(3, 'job-1', 'SUCCEEDED'));
      stream.emitJob(event(4, 'job-2', 'RUNNING'));
    });
    expect(await screen.findAllByRole('button', { name: 'Hủy' })).toHaveLength(1);

    act(() => stream.emitJob(event(5, 'job-2', 'SUCCEEDED')));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Hủy' })).not.toBeInTheDocument());
  });

  it('batch hoàn tất: tổng kết và "Dọn trạng thái" chỉ clear local (không gọi DELETE server)', async () => {
    const calls = mockFetch({ batchJobIds: ['job-1', 'job-2', 'job-3', 'job-4'] });
    const store = makeStore();
    const view = render(<BatchQueue projectId="p1" store={store} />);
    const stream = await queuedStream();

    act(() => {
      stream.emitJob(event(1, 'job-1', 'SUCCEEDED'));
      stream.emitJob(event(2, 'job-2', 'FAILED', 'HTTP_500'));
      stream.emitJob(event(3, 'job-3', 'CANCELED'));
      stream.emitJob(event(4, 'job-4', 'BILLING_UNKNOWN'));
    });

    const summary = await screen.findByLabelText('Kết quả batch');
    expect(summary).toHaveTextContent('1/4 thành công');
    expect(summary).toHaveTextContent('1 thất bại');
    expect(summary).toHaveTextContent('1 đã hủy');
    expect(summary).toHaveTextContent('1 chưa rõ phí');

    fireEvent.click(screen.getByRole('button', { name: 'Dọn trạng thái' }));
    await waitFor(() => expect(screen.queryByText('Tiến độ batch')).not.toBeInTheDocument());
    // Dọn chỉ local: không có request nào mang method DELETE.
    expect(calls.every((call) => (call.init?.method ?? 'GET') !== 'DELETE')).toBe(true);
    // Hủy đăng ký sạch sẽ.
    expect(store.subscriberCount).toBe(0);
    view.unmount();
  });

  it('unmount không lỗi và đóng stream (không rò subscription)', async () => {
    const store = makeStore();
    const { view } = await enqueueAndGetStream({ store });

    view.unmount();
    expect(StubStream.instances[0].closed).toBe(true);
    expect(store.subscriberCount).toBe(0);
  });

  it('cap 1.000 sự kiện vẫn hoạt động: vượt ngưỡng hiện cảnh báo truncated', async () => {
    const many = Array.from({ length: MAX_EVENTS }, (_, index) =>
      event(index + 1, index % 2 === 0 ? 'job-1' : 'job-2', 'RUNNING'),
    );
    await enqueueAndGetStream({ store: makeStore(many) });
    const stream = StubStream.instances[0];

    act(() => stream.emitJob(event(MAX_EVENTS + 1, 'job-1', 'SUCCEEDED')));

    expect(await screen.findByText(/Chỉ giữ 1\.000 sự kiện gần nhất/)).toBeVisible();
    // Bảng vẫn hiển thị đầy đủ job của batch.
    expect(screen.getAllByRole('row')).toHaveLength(3); // header + 2 job
  });
});

describe('BatchQueue (giữ hành vi cũ)', () => {
  it('guard IDs: thiếu cloud authorization chặn enqueue và không gọi /api/batches', async () => {
    const calls = mockFetch();
    render(<BatchQueue projectId="p1" store={makeStore()} />);

    fireEvent.click(await screen.findByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: 'Queue translate' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('BATCH_CLOUD_AUTHORIZATION_REQUIRED');
    expect(calls.some((call) => call.url === '/api/batches')).toBe(false);
  });

  it('chọn chapter, POST /api/batches đúng body và phân trang Load more', async () => {
    const calls = mockFetch({ chaptersNextCursor: 'c2' });
    render(<BatchQueue projectId="p1" store={makeStore()} />);

    fireEvent.click(await screen.findByRole('checkbox'));
    expect(screen.getByText('1/50 selected')).toBeVisible();

    await fillGuardsAndQueue();
    const batchCall = calls.find((call) => call.url === '/api/batches');
    expect(batchCall).toBeTruthy();
    const body = JSON.parse(String(batchCall?.init?.body)) as { projectId: string; chapterIds: string[]; stage: string };
    expect(body.projectId).toBe('p1');
    expect(body.chapterIds).toEqual(['ch-1']);
    expect(body.stage).toBe('TRANSLATE');

    fireEvent.click(screen.getByRole('button', { name: 'Load more' }));
    expect(await screen.findByText('Chapter 2')).toBeVisible();
    fireEvent.click(screen.getAllByRole('checkbox')[1]);
    expect(await screen.findByText('2/50 selected')).toBeVisible();
  });

  it('BILLING_UNKNOWN không bao giờ nhận nút thử lại', async () => {
    const calls = mockFetch({ batchJobIds: ['job-1', 'job-2'] });
    render(<BatchQueue projectId="p1" store={makeStore()} />);
    const stream = await queuedStream();

    act(() => {
      stream.emitJob(event(1, 'job-1', 'RUNNING'));
      stream.emitJob(event(2, 'job-2', 'RUNNING'));
      stream.emitJob(event(3, 'job-1', 'BILLING_UNKNOWN'));
    });

    expect(await screen.findByText('Chưa rõ phí')).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Thử lại' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Thử lại tất cả lỗi retryable' })).not.toBeInTheDocument();
    expect(calls.some((call) => call.url.endsWith('/retry'))).toBe(false);
  });
});

describe('BatchQueue server-side chapter filter (U03/V02)', () => {
  it('sends q to the server instead of filtering mounted rows', async () => {
    const calls = mockFetch();
    render(<BatchQueue projectId="project-1" store={makeStore()} />);
    await screen.findByText('Chapter 1');

    fireEvent.change(screen.getByLabelText('Tìm chương (lọc ở server)'), { target: { value: '  Chương  ' } });

    await waitFor(() => {
      const last = calls.filter((call) => call.url.includes('/chapters?')).at(-1);
      expect(last?.url).toContain('q=Ch%C6%B0%C6%A1ng');
    });
    // The filter is a REQUEST parameter, never a client-side row filter.
    expect(screen.getByText(/Đang lọc ở server/)).toBeVisible();
  });

  it('sends status as an uppercase enum value', async () => {
    const calls = mockFetch();
    render(<BatchQueue projectId="project-1" store={makeStore()} />);
    await screen.findByText('Chapter 1');

    fireEvent.change(screen.getByLabelText('Trạng thái'), { target: { value: 'READY_TO_EXPORT' } });

    await waitFor(() => {
      const last = calls.filter((call) => call.url.includes('/chapters?')).at(-1);
      expect(last?.url).toContain('status=READY_TO_EXPORT');
    });
  });

  it('combines q and status and drops the cursor when the filter changes', async () => {
    const calls = mockFetch({ chaptersNextCursor: 'cursor-1' });
    render(<BatchQueue projectId="project-1" store={makeStore()} />);
    await screen.findByText('Chapter 1');

    fireEvent.click(screen.getByRole('button', { name: 'Load more' }));
    await waitFor(() => {
      const paged = calls.filter((call) => call.url.includes('cursor=')).at(-1);
      expect(paged?.url).toContain('cursor=cursor-1');
      expect(paged?.url).not.toContain('q=');
    });

    fireEvent.change(screen.getByLabelText('Trạng thái'), { target: { value: 'NORMALIZED' } });
    await waitFor(() => {
      const filtered = calls.filter((call) => call.url.includes('/chapters?')).at(-1);
      expect(filtered?.url).toContain('status=NORMALIZED');
      // A new filter restarts paging: the old cursor must not leak in.
      expect(filtered?.url).not.toContain('cursor=');
    });
  });

  it('carries the active filter into paginated requests', async () => {
    const calls = mockFetch({ chaptersNextCursor: 'cursor-2', cursorOnEveryCall: true });
    render(<BatchQueue projectId="project-1" store={makeStore()} />);
    await screen.findByText('Chapter 1');

    fireEvent.change(screen.getByLabelText('Tìm chương (lọc ở server)'), { target: { value: 'kiem' } });
    await waitFor(() => {
      const filtered = calls.filter((call) => call.url.includes('/chapters?')).at(-1);
      expect(filtered?.url).toContain('q=kiem');
    });

    // Paging continues INSIDE the filtered set: the next page keeps the filter.
    fireEvent.click(await screen.findByRole('button', { name: 'Load more' }));
    await waitFor(() => {
      const paged = calls.filter((call) => call.url.includes('cursor=')).at(-1);
      expect(paged?.url).toContain('q=kiem');
    });
  });

  it('shows the unfiltered state before any filter is set', async () => {
    mockFetch();
    render(<BatchQueue projectId="project-1" store={makeStore()} />);
    await screen.findByText('Chapter 1');

    expect(screen.getByText(/Không lọc/)).toBeVisible();
  });
});
