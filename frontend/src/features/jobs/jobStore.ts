/**
 * U07: one shared job-event store for the whole app.
 *
 * Why a store instead of an effect per component:
 * - the SSE subscription is **ref-counted**, so switching tabs (unmount/remount)
 *   or adding a second consumer never multiplies listeners or connections;
 * - the buffer is **bounded** (`MAX_EVENTS = 1000`): the drawer can show the
 *   latest activity without growing the heap forever, and a `truncated` flag
 *   says older metadata was dropped;
 * - reconnecting re-fetches the snapshot first (a gap in the stream therefore
 *   cannot silently drop a job) and resumes from the last `sequenceId` cursor;
 * - replayed events (`sequenceId` not newer than the cursor) are ignored, so a
 *   reconnect never duplicates a job row.
 *
 * The store is transport-agnostic: fetch/stream/timer are injected, which keeps
 * the reconnect and cleanup behaviour testable without a browser.
 */

import { useEffect, useState } from 'react';

import { apiJson } from '../../shared/api';

export const MAX_EVENTS = 1000;

export type JobEvent = {
  sequenceId: string | number;
  jobId: string;
  status: string;
  current: number;
  total: number;
  errorCode?: string | null;
  kind?: string | null;
};

export type ConnectionState = 'offline' | 'connecting' | 'connected';

export type JobStoreSnapshot = {
  events: JobEvent[];
  state: ConnectionState;
  truncated: boolean;
  cursor: string | null;
};

export type StreamLike = {
  addEventListener: (name: string, listener: (message: MessageEvent) => void) => void;
  close: () => void;
  onopen?: (() => void) | null;
  onerror?: (() => void) | null;
};

export type StoreDeps = {
  fetchSnapshot: () => Promise<JobEvent[]>;
  openStream: (url: string) => StreamLike | null;
  schedule: (callback: () => void, delayMs: number) => unknown;
  cancelScheduled: (handle: unknown) => void;
  backoffMs?: number[];
};

const DEFAULT_BACKOFF_MS = [1000, 2000, 4000, 8000];

/**
 * Error codes the backend classifier would actually retry
 * (backend/app/modules/jobs/retry.py: RETRYABLE_CODES plus HTTP_408/5xx and the
 * rate-limit code). This is an ALLOWLIST on purpose: a denylist would offer a
 * "Thử lại" button for codes such as WORKER_HANDLER_EXCEPTION that the API
 * refuses, so the operator would click an action that can only answer 409.
 */
const RETRYABLE_CODES = ['PROVIDER_NETWORK', 'PROVIDER_5XX', 'PROVIDER_TIMEOUT', 'DB_BUSY', 'PROVIDER_RATE_LIMIT'];

function isRetryableCode(code: string | null | undefined): boolean {
  const value = code ?? '';
  if (value === 'HTTP_429') {
    return true;
  }
  if (RETRYABLE_CODES.includes(value)) {
    return true;
  }
  return /^HTTP_5\d\d$/.test(value) || value === 'HTTP_408';
}

export function defaultDeps(): StoreDeps {
  return {
    fetchSnapshot: async () => {
      const payload = await apiJson<{ events: JobEvent[] }>('/api/jobs/snapshot');
      return payload.events ?? [];
    },
    openStream: (url: string) => {
      const Source = (globalThis as { EventSource?: new (url: string) => StreamLike }).EventSource;
      if (!Source) {
        return null;
      }
      return new Source(url);
    },
    schedule: (callback, delayMs) => setTimeout(callback, delayMs),
    cancelScheduled: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
  };
}

export class JobEventStore {
  private readonly deps: StoreDeps;
  private readonly listeners = new Set<(snapshot: JobStoreSnapshot) => void>();
  private events: JobEvent[] = [];
  private state: ConnectionState = 'offline';
  private truncated = false;
  private cursor: string | null = null;
  private stream: StreamLike | null = null;
  private reconnectHandle: unknown = null;
  private attempt = 0;
  private started = false;

  constructor(deps: StoreDeps = defaultDeps()) {
    this.deps = deps;
  }

  subscribe(listener: (snapshot: JobStoreSnapshot) => void): () => void {
    this.listeners.add(listener);
    if (!this.started) {
      this.started = true;
      void this.start();
    }
    listener(this.snapshot());
    return () => {
      this.listeners.delete(listener);
      if (this.listeners.size === 0) {
        this.stop();
      }
    };
  }

  snapshot(): JobStoreSnapshot {
    return {
      events: [...this.events],
      state: this.state,
      truncated: this.truncated,
      cursor: this.cursor,
    };
  }

  /** Number of live subscribers (tests and diagnostics). */
  get subscriberCount(): number {
    return this.listeners.size;
  }

  /**
   * Re-read the durable job state (U07).
   *
   * A cancel/retry action changes a job through the API, and the feed keeps one
   * marker per job, so live stream consumers cannot rely on a new event arriving:
   * the screen asks for the authoritative snapshot instead of guessing the new
   * status locally.
   */
  async refresh(): Promise<void> {
    // The feed carries the CURRENT projection and a job keeps its marker row, so
    // a state change can arrive under a sequence the buffer already holds.
    // Re-reading is therefore a replace, not an append with replay dedupe —
    // but only AFTER the read succeeds: a failed refresh must not wipe the list
    // an operator is looking at.
    let events: JobEvent[];
    try {
      events = await this.deps.fetchSnapshot();
    } catch {
      this.setState('offline');
      return;
    }
    this.events = [];
    this.cursor = null;
    this.truncated = false;
    this.append(events, { reset: true });
    this.setState(this.stream ? 'connected' : 'connecting');
  }

  async start(): Promise<void> {
    await this.refreshSnapshot();
    this.openStream();
  }

  private async refreshSnapshot(): Promise<void> {
    try {
      const events = await this.deps.fetchSnapshot();
      this.append(events, { reset: this.events.length === 0 });
      this.setState(this.stream ? 'connected' : 'connecting');
    } catch {
      this.setState('offline');
    }
  }

  private openStream(): void {
    const url = this.cursor ? `/api/events?after=${encodeURIComponent(this.cursor)}` : '/api/events';
    const stream = this.deps.openStream(url);
    if (stream === null) {
      this.setState('offline');
      return;
    }
    this.stream = stream;
    this.setState('connecting');
    stream.onopen = () => {
      this.attempt = 0;
      this.setState('connected');
    };
    stream.onerror = () => {
      this.setState('offline');
      this.scheduleReconnect();
    };
    stream.addEventListener('job', (message) => {
      try {
        this.append([JSON.parse(message.data) as JobEvent]);
      } catch {
        this.setState('offline');
      }
    });
  }

  private scheduleReconnect(): void {
    if (!this.started || this.reconnectHandle !== null) {
      return;
    }
    const backoff = this.deps.backoffMs ?? DEFAULT_BACKOFF_MS;
    const delay = backoff[Math.min(this.attempt, backoff.length - 1)];
    this.attempt += 1;
    this.setState('connecting');
    this.reconnectHandle = this.deps.schedule(() => {
      this.reconnectHandle = null;
      this.stream?.close();
      this.stream = null;
      // A gap is closed by re-reading the snapshot before resuming the stream.
      void this.refreshSnapshot().then(() => this.openStream());
    }, delay);
  }

  private stop(): void {
    if (this.listeners.size > 0) {
      // A newer subscriber already took over: its connection must stay alive.
      return;
    }
    this.started = false;
    this.stream?.close();
    this.stream = null;
    if (this.reconnectHandle !== null) {
      this.deps.cancelScheduled(this.reconnectHandle);
      this.reconnectHandle = null;
    }
    this.attempt = 0;
    this.events = [];
    this.truncated = false;
    this.cursor = null;
    this.setState('offline');
  }

  private append(incoming: JobEvent[], options: { reset?: boolean } = {}): void {
    if (options.reset) {
      this.events = [];
    }
    let changed = false;
    for (const event of incoming) {
      if (this.isReplay(event)) {
        continue;
      }
      this.events.push(event);
      this.cursor = String(event.sequenceId);
      changed = true;
    }
    if (!changed && incoming.length === 0 && !options.reset) {
      return;
    }
    if (this.events.length > MAX_EVENTS) {
      this.events = this.events.slice(-MAX_EVENTS);
      this.truncated = true;
    }
    this.emit();
  }

  private isReplay(event: JobEvent): boolean {
    if (this.cursor === null) {
      return false;
    }
    return !isNewerSequence(event.sequenceId, this.cursor);
  }

  private setState(state: ConnectionState): void {
    if (this.state === state) {
      return;
    }
    this.state = state;
    this.emit();
  }

  private emit(): void {
    const snapshot = this.snapshot();
    for (const listener of this.listeners) {
      listener(snapshot);
    }
  }
}

/** True when `next` is strictly newer than `current` (numeric when possible). */
export function isNewerSequence(next: string | number, current: string | null): boolean {
  if (current === null) {
    return true;
  }
  const nextNumber = Number(next);
  const currentNumber = Number(current);
  if (Number.isFinite(nextNumber) && Number.isFinite(currentNumber)) {
    return nextNumber > currentNumber;
  }
  return String(next) > current;
}

/**
 * Failed jobs that a retry action may pick up: the latest event per job, FAILED,
 * with a retryable error code. Non-retryable failures stay out so the UI never
 * offers a retry that the backend would refuse (U07 batch retry).
 */
export function retryableFailures(events: JobEvent[]): JobEvent[] {
  const latest = new Map<string, JobEvent>();
  for (const event of events) {
    const existing = latest.get(event.jobId);
    if (!existing || isNewerSequence(event.sequenceId, String(existing.sequenceId))) {
      latest.set(event.jobId, event);
    }
  }
  return [...latest.values()].filter(
    (event) => event.status === 'FAILED' && isRetryableCode(event.errorCode),
  );
}

/** React binding: the store owns the connection, the hook owns the subscription.
 * A `null`/undefined store falls back to the app-wide one, which lets a caller
 * disable the subscription explicitly by passing `null`.
 */
export function useJobEvents(store?: JobEventStore | null): JobStoreSnapshot {
  const active = store === undefined ? defaultJobEventStore : store;
  const [snapshot, setSnapshot] = useState<JobStoreSnapshot>(() =>
    active ? active.snapshot() : { events: [], state: 'offline', truncated: false, cursor: null },
  );
  useEffect(() => {
    if (!active) {
      return undefined;
    }
    return active.subscribe(setSnapshot);
  }, [active]);
  return snapshot;
}

export const defaultJobEventStore = new JobEventStore();
