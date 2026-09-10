import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { useEffect, useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  JobEventStore,
  MAX_EVENTS,
  isNewerSequence,
  retryableFailures,
  useJobEvents,
  type JobEvent,
  type StreamLike,
} from './jobStore';

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

  fail(): void {
    this.onerror?.();
  }
}

function event(sequenceId: number | string, jobId = 'job-1', status = 'RUNNING'): JobEvent {
  return { sequenceId, jobId, status, current: 1, total: 3, errorCode: null };
}

function makeStore(options: { events?: JobEvent[]; backoffMs?: number[] } = {}) {
  const timers: { callback: () => void; delay: number; cancelled: boolean }[] = [];
  const snapshots: number[] = [];
  const store = new JobEventStore({
    fetchSnapshot: async () => {
      snapshots.push(1);
      return options.events ?? [event(1)];
    },
    openStream: (url) => new StubStream(url),
    schedule: (callback, delayMs) => {
      const entry = { callback, delay: delayMs, cancelled: false };
      timers.push(entry);
      return entry;
    },
    cancelScheduled: (handle) => {
      (handle as { cancelled: boolean }).cancelled = true;
    },
    backoffMs: options.backoffMs,
  });
  return { store, timers, snapshotCalls: snapshots };
}

beforeEach(() => {
  StubStream.instances = [];
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('JobEventStore (U07 round 2)', () => {
  it('keeps a single connection for many subscribers and closes it after the last one leaves', async () => {
    const { store } = makeStore();
    const first = store.subscribe(() => undefined);
    const second = store.subscribe(() => undefined);

    await waitFor(() => expect(StubStream.instances).toHaveLength(1));
    expect(store.subscriberCount).toBe(2);

    first();
    expect(StubStream.instances[0].closed).toBe(false);

    second();
    await waitFor(() => expect(StubStream.instances[0].closed).toBe(true));
    expect(store.subscriberCount).toBe(0);
    expect(store.snapshot().state).toBe('offline');
  });

  it('does not multiply listeners when a consumer unmounts and remounts (tab switching)', async () => {
    const { store } = makeStore();

    function Consumer() {
      const snapshot = useJobEvents(store);
      return <p>{snapshot.events.length} sự kiện · {snapshot.state}</p>;
    }

    const view = render(<Consumer />);
    await waitFor(() => expect(StubStream.instances).toHaveLength(1));
    const firstStream = StubStream.instances[0];
    expect(await screen.findByText(/1 sự kiện/)).toBeVisible();

    view.unmount();
    await waitFor(() => expect(firstStream.closed).toBe(true));

    render(<Consumer />);
    await waitFor(() => expect(StubStream.instances).toHaveLength(2));
    // Reconnecting for the same store must not keep the dead stream around and
    // must leave exactly one live connection.
    await waitFor(() => {
      expect(StubStream.instances.filter((stream) => !stream.closed)).toHaveLength(1);
    });
    expect(store.snapshot().events).toHaveLength(1);
  });

  it('ignores replayed events after a reconnect and follows the cursor', async () => {
    const { store, timers } = makeStore({ events: [event(5)] });
    const unsubscribe = store.subscribe(() => undefined);
    await waitFor(() => expect(StubStream.instances).toHaveLength(1));

    expect(StubStream.instances[0].url).toBe('/api/events?after=5');
    act(() => StubStream.instances[0].emitJob(event(6, 'job-2')));
    expect(store.snapshot().events.map((item) => item.jobId)).toEqual(['job-1', 'job-2']);

    // A reconnect replays the snapshot cursor: the same event must not duplicate.
    act(() => StubStream.instances[0].emitJob(event(5)));
    expect(store.snapshot().events).toHaveLength(2);

    act(() => StubStream.instances[0].fail());
    expect(store.snapshot().state).toBe('connecting');
    expect(timers[0].delay).toBe(1000);

    // The scheduled reconnect re-reads the snapshot before resuming the stream,
    // which is what closes a gap in the stream.
    const before = store.snapshot().events.length;
    await act(async () => {
      await timers[0].callback();
    });
    expect(store.snapshot().events.length).toBeGreaterThanOrEqual(before);
    unsubscribe();
  });

  it('backs off and stops reconnecting once nobody is subscribed', async () => {
    const { store, timers } = makeStore({ backoffMs: [500, 1000] });
    const unsubscribe = store.subscribe(() => undefined);
    await waitFor(() => expect(StubStream.instances).toHaveLength(1));

    act(() => StubStream.instances[0].fail());
    expect(timers[0].delay).toBe(500);
    unsubscribe();

    expect(timers[0].cancelled).toBe(true);
    expect(store.snapshot()).toEqual({ events: [], state: 'offline', truncated: false, cursor: null });
  });

  it('bounds the buffer at 1000 events and reports truncation', async () => {
    const { store } = makeStore({ events: [] });
    store.subscribe(() => undefined);
    await waitFor(() => expect(StubStream.instances).toHaveLength(1));

    const stream = StubStream.instances[0];
    act(() => {
      for (let index = 1; index <= MAX_EVENTS + 5; index += 1) {
        stream.emitJob(event(index, `job-${index}`));
      }
    });

    const snapshot = store.snapshot();
    expect(snapshot.events).toHaveLength(MAX_EVENTS);
    expect(snapshot.truncated).toBe(true);
    expect(snapshot.events.at(-1)?.jobId).toBe(`job-${MAX_EVENTS + 5}`);
    expect(snapshot.cursor).toBe(String(MAX_EVENTS + 5));
  });

  it('does not duplicate metadata when two tabs consume the same store', async () => {
    const { store } = makeStore({ events: [event(1)] });

    function Consumer({ label }: { label: string }) {
      const snapshot = useJobEvents(store);
      return <p>{label}:{snapshot.events.length}</p>;
    }

    function Tabs() {
      const [showSecond, setShowSecond] = useState(false);
      useEffect(() => {
        setShowSecond(true);
      }, []);
      return (
        <>
          <Consumer label="a" />
          {showSecond ? <Consumer label="b" /> : null}
        </>
      );
    }

    render(<Tabs />);

    await waitFor(() => expect(StubStream.instances).toHaveLength(1));
    expect(await screen.findByText('a:1')).toBeVisible();
    expect(await screen.findByText('b:1')).toBeVisible();
    expect(store.subscriberCount).toBe(2);
  });
});

describe('retryableFailures (U07 batch retry)', () => {
  it('returns the latest failure per job and skips non-retryable codes', () => {
    const events: JobEvent[] = [
      { ...event(1, 'job-ok', 'FAILED'), errorCode: 'HTTP_500' },
      { ...event(2, 'job-ok', 'RUNNING'), errorCode: null },
      { ...event(3, 'job-bad', 'FAILED'), errorCode: 'HTTP_400' },
      { ...event(4, 'job-timeout', 'FAILED'), errorCode: 'PROVIDER_TIMEOUT' },
      { ...event(5, 'job-plain', 'FAILED'), errorCode: null },
      { ...event(6, 'job-done', 'SUCCEEDED'), errorCode: null },
    ];

    const retryable = retryableFailures(events).map((item) => item.jobId);

    expect(retryable.sort()).toEqual(['job-plain', 'job-timeout']);
  });

  it('never offers a retry for a job that later succeeded', () => {
    const events: JobEvent[] = [
      { ...event(1, 'job-1', 'FAILED'), errorCode: 'HTTP_500' },
      { ...event(2, 'job-1', 'SUCCEEDED'), errorCode: null },
    ];

    expect(retryableFailures(events)).toEqual([]);
  });
});

describe('isNewerSequence', () => {
  it('compares padded numeric ids and falls back to string order', () => {
    expect(isNewerSequence('002', '001')).toBe(true);
    expect(isNewerSequence('001', '002')).toBe(false);
    expect(isNewerSequence('001', '001')).toBe(false);
    expect(isNewerSequence('abc', null)).toBe(true);
    expect(isNewerSequence('b', 'a')).toBe(true);
    expect(isNewerSequence('a', 'b')).toBe(false);
  });
});
