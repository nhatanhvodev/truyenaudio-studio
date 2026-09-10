import { useCallback, useEffect, useRef, useState } from 'react';

import { apiJson } from '../../shared/api';

export type DraftFrame = { offset: number; text: string };

export type DraftSnapshot = {
  offset: number;
  text: string;
  resync?: boolean;
  status?: string;
  truncated?: boolean;
  approvable?: boolean;
  draftRevision?: number | null;
};

export type JobDraftState = {
  text: string;
  offset: number;
  status: string;
  approvable: boolean;
  truncated: boolean;
  draftRevision: number | null;
  loading: boolean;
  connected: boolean;
  error: string;
  /** Force a snapshot resync (used after a gap or a reconnect). */
  resync: () => Promise<void>;
};

type Options = {
  jobId: string;
  /** Injected for tests; defaults to the global EventSource. */
  eventSourceFactory?: (url: string) => EventSourceLike | null;
};

/** Minimal EventSource surface used by the hook (keeps tests independent). */
export type EventSourceLike = {
  addEventListener: (type: string, listener: (event: MessageEvent) => void) => void;
  close: () => void;
  onerror?: ((event: unknown) => void) | null;
  onopen?: ((event: unknown) => void) | null;
};

/**
 * U07: consume the job draft stream (J04) with resume-by-offset semantics.
 *
 * - the first snapshot comes from `GET /api/jobs/{id}/draft`, so a client that
 *   reconnects never causes the provider to run again;
 * - `draft` events append by offset; a replayed frame (`offset <= current`) is
 *   ignored and a `gap` event replaces the buffer with the server snapshot;
 * - `terminal` marks the stream finished (and closes it) while keeping the text;
 * - a draft is **never** approvable: the panel that renders this state must send
 *   the user back to the translation review to approve anything.
 */
export function useJobDraftStream({ jobId, eventSourceFactory }: Options): JobDraftState {
  const [text, setText] = useState('');
  const [offset, setOffset] = useState(0);
  const [status, setStatus] = useState('DRAFT');
  const [approvable, setApprovable] = useState(false);
  const [truncated, setTruncated] = useState(false);
  const [draftRevision, setDraftRevision] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState('');

  const offsetRef = useRef(0);
  offsetRef.current = offset;

  const applySnapshot = useCallback((snapshot: DraftSnapshot) => {
    setText(snapshot.text ?? '');
    setOffset(snapshot.offset ?? 0);
    offsetRef.current = snapshot.offset ?? 0;
    if (snapshot.status) {
      setStatus(snapshot.status);
    }
    if (snapshot.truncated !== undefined) {
      setTruncated(snapshot.truncated === true);
    }
    if (snapshot.approvable !== undefined) {
      setApprovable(snapshot.approvable === true);
    }
    if (snapshot.draftRevision !== undefined) {
      setDraftRevision(snapshot.draftRevision ?? null);
    }
  }, []);

  const resync = useCallback(async (): Promise<void> => {
    try {
      const snapshot = await apiJson<DraftSnapshot>(
        `/api/jobs/${jobId}/draft?afterOffset=${offsetRef.current}`,
      );
      applySnapshot(snapshot);
      setError('');
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : 'DRAFT_SNAPSHOT_FAILED');
    }
  }, [applySnapshot, jobId]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setText('');
    setOffset(0);
    offsetRef.current = 0;
    setStatus('DRAFT');
    setApprovable(false);
    setTruncated(false);
    setDraftRevision(null);
    setError('');

    apiJson<DraftSnapshot>(`/api/jobs/${jobId}/draft`)
      .then((snapshot) => {
        if (!cancelled) {
          applySnapshot(snapshot);
        }
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : 'DRAFT_SNAPSHOT_FAILED');
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });

    const create =
      eventSourceFactory ??
      ((url: string) => {
        const Source = (globalThis as { EventSource?: new (url: string) => EventSourceLike }).EventSource;
        return Source ? new Source(url) : null;
      });
    let source: EventSourceLike | null = null;
    try {
      source = create(`/api/jobs/${jobId}/draft/stream?afterOffset=${offsetRef.current}`);
    } catch {
      source = null;
    }

    if (source) {
      source.onopen = () => {
        if (!cancelled) {
          setConnected(true);
        }
      };
      source.onerror = () => {
        if (!cancelled) {
          setConnected(false);
        }
      };
      source.addEventListener('draft', (event: MessageEvent) => {
        if (cancelled) {
          return;
        }
        const frame = parseFrame(event.data);
        if (frame === null) {
          return;
        }
        if (frame.offset <= offsetRef.current) {
          // Duplicate/replayed frame: never append twice.
          return;
        }
        offsetRef.current = frame.offset;
        setOffset(frame.offset);
        setText((existing) => existing + frame.text);
      });
      source.addEventListener('gap', (event: MessageEvent) => {
        if (!cancelled) {
          applySnapshot(parseSnapshot(event.data));
        }
      });
      source.addEventListener('terminal', (event: MessageEvent) => {
        if (cancelled) {
          return;
        }
        const payload = parseSnapshot(event.data);
        if (payload.status) {
          setStatus(payload.status);
        }
        setApprovable(payload.approvable === true);
        setConnected(false);
        source?.close();
      });
    }

    return () => {
      cancelled = true;
      source?.close();
    };
  }, [applySnapshot, eventSourceFactory, jobId]);

  return {
    text,
    offset,
    status,
    approvable,
    truncated,
    draftRevision,
    loading,
    connected,
    error,
    resync,
  };
}

function parseFrame(raw: unknown): DraftFrame | null {
  const value = parseSnapshot(raw);
  const offset = value.offset;
  const text = value.text;
  if (typeof offset !== 'number' || typeof text !== 'string') {
    return null;
  }
  return { offset, text };
}

function parseSnapshot(raw: unknown): DraftSnapshot {
  if (typeof raw !== 'string' || raw === '') {
    return { offset: 0, text: '' };
  }
  try {
    const parsed = JSON.parse(raw) as Partial<DraftSnapshot>;
    return {
      offset: typeof parsed.offset === 'number' ? parsed.offset : 0,
      text: typeof parsed.text === 'string' ? parsed.text : '',
      status: typeof parsed.status === 'string' ? parsed.status : undefined,
      truncated: parsed.truncated === true,
      approvable: parsed.approvable === true,
      draftRevision: typeof parsed.draftRevision === 'number' ? parsed.draftRevision : null,
      resync: parsed.resync === true,
    };
  } catch {
    return { offset: 0, text: '' };
  }
}
