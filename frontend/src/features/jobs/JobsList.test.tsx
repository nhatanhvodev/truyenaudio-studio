import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { JobEventStore, MAX_EVENTS } from './jobStore';
import JobsList, { latestByJob } from './JobsList';
import type { JobEvent, StreamLike } from './jobStore';

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
}

function event(sequenceId: number, jobId: string, status: string, errorCode: string | null = null): JobEvent {
  return { sequenceId, jobId, status, current: 1, total: 4, errorCode, kind: 'TRANSLATE' };
}

function storeWith(events: JobEvent[]): JobEventStore {
  return new JobEventStore({
    fetchSnapshot: async () => events,
    openStream: (url) => new StubStream(url),
    schedule: () => 0,
    cancelScheduled: () => undefined,
  });
}

function renderList(store: JobEventStore, props: { onRetry?: (event: JobEvent) => void; onCancel?: (event: JobEvent) => void } = {}) {
  return render(
    <MemoryRouter>
      <JobsList store={store} {...props} />
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  StubStream.instances = [];
});

describe('JobsList (U07 round 2)', () => {
  it('collapses events to one row per job with the latest status', async () => {
    const store = storeWith([
      event(1, 'job-1', 'RUNNING'),
      event(2, 'job-1', 'SUCCEEDED'),
      event(3, 'job-2', 'RUNNING'),
      event(4, 'job-3', 'FAILED', 'HTTP_500'),
    ]);

    renderList(store);

    await waitFor(() => expect(screen.getAllByRole('row')).toHaveLength(4)); // header + 3 jobs
    expect(screen.getByText('Hoàn tất')).toBeVisible();
    expect(screen.getByText('Đang chạy')).toBeVisible();
    expect(screen.queryByText('Đã hủy')).not.toBeInTheDocument();
  });

  it('offers retry only for retryable failures and cancel only while running', async () => {
    const store = storeWith([
      event(1, 'job-retry', 'FAILED', 'HTTP_500'),
      event(2, 'job-no-retry', 'FAILED', 'HTTP_400'),
      event(3, 'job-running', 'RUNNING'),
      event(4, 'job-canceling', 'CANCEL_REQUESTED'),
    ]);
    const onRetry = vi.fn();
    const onCancel = vi.fn();

    renderList(store, { onRetry, onCancel });
    await waitFor(() => expect(screen.getByText('Thử lại')).toBeVisible());

    // Exactly one retry button (job-retry), never for HTTP_400.
    expect(screen.getAllByRole('button', { name: 'Thử lại' })).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: 'Thử lại' }));
    expect(onRetry).toHaveBeenCalledWith(expect.objectContaining({ jobId: 'job-retry' }));

    // Cancel is only offered while the job can still be stopped: a job that is
    // already being canceled shows the label but no second cancel action.
    expect(screen.getAllByRole('button', { name: 'Hủy job' })).toHaveLength(1);
    expect(screen.getByText('Đang hủy…')).toBeVisible();
  });

  it('links a job to its draft view and reports the stream state', async () => {
    const store = storeWith([event(1, 'job-1', 'RUNNING')]);

    renderList(store);

    const link = await screen.findByRole('link', { name: 'job-1' });
    expect(link).toHaveAttribute('href', '/jobs/job-1/draft');
    expect(screen.getByLabelText('Trạng thái luồng')).toBeVisible();
  });

  it('says so when the event buffer was truncated', async () => {
    const many = Array.from({ length: MAX_EVENTS + 1 }, (_, index) =>
      event(index + 1, `job-${index}`, 'SUCCEEDED'),
    );
    const store = storeWith(many);

    renderList(store);

    expect(await screen.findByText(/Chỉ giữ 1\.000 sự kiện gần nhất/)).toBeVisible();
  });
});

describe('latestByJob', () => {
  it('keeps the newest event per job and sorts by operational priority', () => {
    const jobs = latestByJob([
      event(1, 'a', 'SUCCEEDED'),
      event(2, 'b', 'FAILED', 'HTTP_500'),
      event(3, 'c', 'RUNNING'),
      event(4, 'a', 'RUNNING'),
    ]);

    // One row per job, newest event wins, and the running jobs come first.
    expect(jobs.map((job) => job.status)).toEqual(['RUNNING', 'RUNNING', 'FAILED']);
    expect(jobs.find((job) => job.jobId === 'a')?.status).toBe('RUNNING');
    expect(jobs.at(-1)?.jobId).toBe('b');
  });
});
