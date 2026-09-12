import { useEffect, useMemo, useState } from 'react';

import { apiJson } from '../../shared/api';
import { retryableFailures, useJobEvents, type JobEvent, type JobEventStore } from '../jobs/jobStore';

import styles from './BatchQueue.module.css';

type ChapterSummary = {
  id: string;
  ordinal: number;
  sourceTitle: string | null;
  translatedTitle: string | null;
  state: string;
  progress: {
    current: number;
    total: number;
  };
  cost: {
    estimatedVnd: number | null;
    actualVnd: number | null;
  };
  issues: number;
  hashes: {
    sourceSha256: string | null;
    translationSha256: string | null;
  };
};

type ChapterPage = {
  items: ChapterSummary[];
  nextCursor: string | null;
  total: number;
};

type BatchView = {
  batchId: string;
  total: number;
  queued: number;
  running: number;
  succeeded: number;
  failed: number;
  canceled: number;
  blocked: number;
  jobIds: string[];
};

type Props = {
  projectId: string;
  /** Shared job-event store (injectable for tests; defaults to the app-wide one). */
  store?: JobEventStore;
  /** Retry override for tests/wiring; defaults to POST /api/jobs/{id}/retry. */
  onRetry?: (event: JobEvent) => void | Promise<void>;
  /** Cancel override for tests/wiring; defaults to POST /api/jobs/{id}/cancel. */
  onCancel?: (event: JobEvent) => void | Promise<void>;
};

const pageLimit = 25;
const maxSelection = 50;

const STATUS_LABEL: Record<string, string> = {
  QUEUED: 'Đang chờ',
  RUNNING: 'Đang chạy',
  CANCEL_REQUESTED: 'Đang hủy…',
  CANCELED: 'Đã hủy',
  SUCCEEDED: 'Hoàn tất',
  FAILED: 'Lỗi',
  BILLING_UNKNOWN: 'Chưa rõ phí',
};

const STATE_LABEL: Record<string, string> = {
  offline: 'Ngoại tuyến',
  connecting: 'Đang kết nối',
  connected: 'Trực tuyến',
};

/**
 * Status colour, never carried alone: every class below lands on a cell that
 * already renders the Vietnamese `STATUS_LABEL` for the same status.
 */
const STATUS_CLASS: Record<string, string> = {
  QUEUED: styles.stateQueued,
  RUNNING: styles.stateRunning,
  CANCEL_REQUESTED: styles.stateRunning,
  CANCELED: styles.stateCanceled,
  SUCCEEDED: styles.stateDone,
  FAILED: styles.stateFailed,
  BILLING_UNKNOWN: styles.stateBlocked,
};

/** Chapter states the server accepts for the `status` filter (uppercase enum values). */
const CHAPTER_STATES = [
  'IMPORTED',
  'NORMALIZED',
  'TRANSLATING',
  'TRANSLATED',
  'QA_REVIEW',
  'READY_FOR_AUDIO',
  'AUDIO_RENDERING',
  'READY_TO_EXPORT',
  'PUBLISHED',
  'ARCHIVED',
] as const;

/** Statuses that can still change: while one is present the batch is not finished. */
const ACTIVE_STATUSES = new Set(['QUEUED', 'RUNNING', 'CANCEL_REQUESTED']);

type TrackedJob = {
  jobId: string;
  event: JobEvent | null;
};

export function BatchQueue({ projectId, store, onRetry, onCancel }: Props) {
  const [items, setItems] = useState<ChapterSummary[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [batch, setBatch] = useState<BatchView | null>(null);
  const [providerProfileId, setProviderProfileId] = useState('');
  const [cloudConsentId, setCloudConsentId] = useState('');
  const [quoteId, setQuoteId] = useState('');
  const [budgetAuthorizationId, setBudgetAuthorizationId] = useState('');
  const [estimatedUnits, setEstimatedUnits] = useState('');
  // U03/V02: the chapter list filters on the SERVER. Filtering only the rows
  // already mounted would silently miss chapters outside the current page.
  const [query, setQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('');

  const filterQuery = useMemo(() => {
    const params = new URLSearchParams();
    const needle = query.trim();
    if (needle) {
      params.set('q', needle);
    }
    if (statusFilter) {
      params.set('status', statusFilter);
    }
    return params.toString();
  }, [query, statusFilter]);

  useEffect(() => {
    let cancelled = false;
    setItems([]);
    setNextCursor(null);
    setSelected(new Set());
    setBatch(null);
    setError('');
    setLoading(true);
    const suffix = filterQuery ? `&${filterQuery}` : '';
    apiJson<ChapterPage>(`/api/projects/${projectId}/chapters?limit=${pageLimit}${suffix}`)
      .then((page) => {
        if (!cancelled) {
          setItems(page.items);
          setNextCursor(page.nextCursor);
        }
      })
      .catch((reason) => {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : 'CHAPTER_PAGE_FAILED');
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, filterQuery]);

  // U07: while a batch is being tracked, subscribe to the ONE shared job-event
  // store (ref-counted, never a second connection); before that, subscribe to
  // nothing so an idle screen does not open the stream at all.
  const { events, state, truncated } = useJobEvents(batch ? (store ?? undefined) : null);

  const selectedIds = useMemo(() => Array.from(selected), [selected]);

  /** Latest event per batch job, in enqueue order; jobs without events stay pending. */
  const tracked = useMemo<TrackedJob[]>(() => {
    if (!batch) {
      return [];
    }
    const ids = new Set(batch.jobIds);
    const latest = new Map<string, JobEvent>();
    for (const event of events) {
      if (!ids.has(event.jobId)) {
        continue;
      }
      latest.set(event.jobId, event);
    }
    return batch.jobIds.map((jobId) => ({ jobId, event: latest.get(jobId) ?? null }));
  }, [batch, events]);

  const retryable = useMemo(
    () => new Set(retryableFailures(events).map((event) => event.jobId)),
    [events],
  );

  const failedRows = useMemo(
    () => tracked.filter((row): row is TrackedJob & { event: JobEvent } => row.event?.status === 'FAILED'),
    [tracked],
  );
  const retryableFailedRows = failedRows.filter((row) => retryable.has(row.jobId));
  const pendingCount = tracked.filter((row) => row.event === null).length;
  const activeCount = tracked.filter((row) => row.event !== null && ACTIVE_STATUSES.has(row.event.status)).length;
  const finished = batch !== null && tracked.length > 0 && activeCount === 0 && pendingCount === 0;

  const counts = useMemo(() => {
    const summary = { succeeded: 0, failed: 0, canceled: 0, blocked: 0 };
    for (const row of tracked) {
      if (row.event?.status === 'SUCCEEDED') {
        summary.succeeded += 1;
      } else if (row.event?.status === 'FAILED') {
        summary.failed += 1;
      } else if (row.event?.status === 'CANCELED') {
        summary.canceled += 1;
      } else if (row.event?.status === 'BILLING_UNKNOWN') {
        summary.blocked += 1;
      }
    }
    return summary;
  }, [tracked]);

  async function loadMore() {
    if (!nextCursor) {
      return;
    }
    setLoading(true);
    setError('');
    try {
      const page = await apiJson<ChapterPage>(
        `/api/projects/${projectId}/chapters?limit=${pageLimit}&cursor=${encodeURIComponent(nextCursor)}${filterQuery ? `&${filterQuery}` : ''}`,
      );
      setItems((current) => [...current, ...page.items]);
      setNextCursor(page.nextCursor);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'CHAPTER_PAGE_FAILED');
    } finally {
      setLoading(false);
    }
  }

  function toggle(chapterId: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(chapterId)) {
        next.delete(chapterId);
        return next;
      }
      if (next.size < maxSelection) {
        next.add(chapterId);
      }
      return next;
    });
  }

  async function queueTranslation() {
    if (selectedIds.length === 0) {
      return;
    }
    const estimate = Number.parseInt(estimatedUnits, 10);
    if (
      !providerProfileId.trim()
      || !cloudConsentId.trim()
      || !quoteId.trim()
      || !budgetAuthorizationId.trim()
      || !Number.isFinite(estimate)
      || estimate <= 0
    ) {
      setError('BATCH_CLOUD_AUTHORIZATION_REQUIRED');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const payload = await apiJson<BatchView>('/api/batches', {
        method: 'POST',
        body: {
          projectId,
          chapterIds: selectedIds,
          stage: 'TRANSLATE',
          quoteId: quoteId.trim(),
          providerProfileId: providerProfileId.trim(),
          cloudConsentId: cloudConsentId.trim(),
          budgetAuthorizationId: budgetAuthorizationId.trim(),
          estimatedUnits: estimate,
          estimatedUnit: 'INPUT_TOKEN',
        },
      });
      setBatch(payload);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'BATCH_QUEUE_FAILED');
    } finally {
      setBusy(false);
    }
  }

  function handleRetry(event: JobEvent) {
    void (async () => {
      if (onRetry) {
        await onRetry(event);
        return;
      }
      await apiJson(`/api/jobs/${encodeURIComponent(event.jobId)}/retry`, { method: 'POST' });
    })().catch((reason) => {
      setError(reason instanceof Error ? reason.message : 'JOB_RETRY_FAILED');
    });
  }

  function handleCancel(event: JobEvent) {
    void (async () => {
      if (onCancel) {
        await onCancel(event);
        return;
      }
      await apiJson(`/api/jobs/${encodeURIComponent(event.jobId)}/cancel`, { method: 'POST' });
    })().catch((reason) => {
      setError(reason instanceof Error ? reason.message : 'JOB_CANCEL_FAILED');
    });
  }

  function retryAllRetryable() {
    // Only jobs retryableFailures() allows — never non-retryable or billingUnknown.
    for (const row of retryableFailedRows) {
      handleRetry(row.event);
    }
  }

  function clearTracking() {
    // Local only: drop the batch handle (and with it the store subscription);
    // nothing is deleted on the server.
    setBatch(null);
  }

  return (
    <section className={styles.shell} aria-label="Batch queue">
      <header className={styles.header}>
        <div>
          <h1 className={styles.title}>Batch queue</h1>
          <p className={styles.meta}>{selected.size}/{maxSelection} selected</p>
        </div>
        <button
          type="button"
          onClick={() => void queueTranslation()}
          disabled={busy || selected.size === 0}
          className={styles.primaryButton}
        >
          Queue translate
        </button>
      </header>

      <section className={styles.guardBox} aria-label="Cloud batch authorization">
        <label className={styles.label}>
          Provider profile ID
          <input value={providerProfileId} onChange={(event) => setProviderProfileId(event.target.value)} className={styles.input} />
        </label>
        <label className={styles.label}>
          Cloud consent ID
          <input value={cloudConsentId} onChange={(event) => setCloudConsentId(event.target.value)} className={styles.input} />
        </label>
        <label className={styles.label}>
          Batch operation ID
          <input value={quoteId} onChange={(event) => setQuoteId(event.target.value)} className={styles.input} />
        </label>
        <label className={styles.label}>
          Budget authorization ID
          <input value={budgetAuthorizationId} onChange={(event) => setBudgetAuthorizationId(event.target.value)} className={styles.input} />
        </label>
        <label className={styles.label}>
          Estimated input tokens
          <input value={estimatedUnits} onChange={(event) => setEstimatedUnits(event.target.value)} inputMode="numeric" className={styles.input} />
        </label>
      </section>

      <section className={styles.filterBox} aria-label="Bộ lọc chương">
        <label className={styles.label}>
          Tìm chương (lọc ở server)
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Tên hoặc tiêu đề chương"
            className={styles.input}
          />
        </label>
        <label className={styles.label}>
          Trạng thái
          <select
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value)}
            className={styles.input}
          >
            <option value="">Tất cả</option>
            {CHAPTER_STATES.map((state) => (
              <option key={state} value={state}>
                {state}
              </option>
            ))}
          </select>
        </label>
      </section>

      <p className={styles.meta} role="status">
        {filterQuery ? 'Đang lọc ở server' : 'Không lọc'} · {items.length} chương đã tải
      </p>

      <div className={styles.list}>
        {items.map((chapter) => (
          <label key={chapter.id} className={styles.row}>
            <input
              type="checkbox"
              checked={selected.has(chapter.id)}
              onChange={() => toggle(chapter.id)}
              className={styles.checkbox}
            />
            <span className={styles.ordinal}>{chapter.ordinal}</span>
            <span className={styles.name}>{chapter.sourceTitle ?? chapter.translatedTitle ?? 'Untitled chapter'}</span>
            <span className={styles.chapterState}>{chapter.state}</span>
            <span className={styles.count}>{chapter.progress.current}/{chapter.progress.total}</span>
            <span className={styles.hash}>{chapter.hashes.sourceSha256?.slice(0, 10) ?? '-'}</span>
          </label>
        ))}
      </div>

      {nextCursor ? (
        <button type="button" onClick={() => void loadMore()} disabled={loading} className={styles.secondaryButton}>
          Load more
        </button>
      ) : null}
      {batch ? <p role="status" className={styles.success}>Queued {batch.total} jobs from {batch.batchId.slice(0, 12)}</p> : null}
      {error ? <p role="alert" className={styles.error}>{error}</p> : null}
      {loading && items.length === 0 ? <p className={styles.meta}>Loading</p> : null}

      {batch ? (
        <section aria-label="Tiến độ batch" className={styles.trackBox}>
          <header className={styles.trackHeader}>
            <h2 className={styles.trackTitle}>Tiến độ batch</h2>
            <span className={styles.streamState} aria-label="Trạng thái luồng batch">{STATE_LABEL[state] ?? state}</span>
          </header>
          {truncated ? (
            <p role="status" className={styles.trackNote}>Chỉ giữ 1.000 sự kiện gần nhất; job cũ hơn không còn trong danh sách.</p>
          ) : null}
          {pendingCount > 0 && !finished ? (
            <p role="status" className={styles.trackNote}>Chờ sự kiện cho {pendingCount} job…</p>
          ) : null}

          <table className={styles.trackTable}>
            <thead>
              <tr>
                <th className={styles.trackTh}>Job</th>
                <th className={styles.trackTh}>Trạng thái</th>
                <th className={styles.trackTh}>Tiến độ</th>
                <th className={styles.trackTh}>Hành động</th>
              </tr>
            </thead>
            <tbody>
              {tracked.map(({ jobId, event }) => (
                <tr key={jobId}>
                  <td className={`${styles.trackTd} ${styles.jobId}`} title={jobId}>{jobId.slice(0, 12)}</td>
                  <td className={`${styles.trackTd} ${styles.state} ${event ? (STATUS_CLASS[event.status] ?? styles.stateQueued) : ''}`}>
                    {event ? STATUS_LABEL[event.status] ?? event.status : 'Chưa có sự kiện'}
                    {event?.errorCode ? <span className={styles.trackError}> · {event.errorCode}</span> : null}
                  </td>
                  <td className={`${styles.trackTd} ${styles.count}`}>{event && event.total > 0 ? `${event.current}/${event.total}` : '—'}</td>
                  <td className={`${styles.trackTd} ${styles.actions}`}>
                    {event && (event.status === 'QUEUED' || event.status === 'RUNNING') ? (
                      <button type="button" onClick={() => handleCancel(event)} className={styles.secondaryButton}>
                        Hủy
                      </button>
                    ) : null}
                    {event && retryable.has(jobId) ? (
                      <button type="button" onClick={() => handleRetry(event)} className={styles.primaryButton}>
                        Thử lại
                      </button>
                    ) : null}
                    {event?.status === 'FAILED' && !retryable.has(jobId) ? (
                      <span className={styles.trackNote}>không thể tự thử lại — hãy mở nháp để xử lý thủ công</span>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {failedRows.length > 0 && batch ? (
            <div role="alert" className={styles.alert}>
              <p className={styles.alertTitle}>{failedRows.length}/{batch.jobIds.length} job thất bại</p>
              <ul className={styles.alertList}>
                {failedRows.map(({ jobId, event }) => (
                  <li key={jobId}>
                    <span title={jobId}>{jobId.slice(0, 12)}</span>
                    {event?.errorCode ? <span className={styles.trackError}> · {event.errorCode}</span> : null}
                    {!retryable.has(jobId) ? <span className={styles.trackNote}> — không thể tự thử lại, hãy mở nháp để xử lý thủ công</span> : null}
                  </li>
                ))}
              </ul>
              {retryableFailedRows.length > 0 ? (
                <button type="button" onClick={retryAllRetryable} className={styles.primaryButton}>
                  Thử lại tất cả lỗi retryable
                </button>
              ) : null}
            </div>
          ) : null}

          {finished ? (
            <section aria-label="Kết quả batch" className={styles.summaryBox}>
              <p className={styles.summaryLine}>
                Hoàn tất: {counts.succeeded}/{batch.jobIds.length} thành công · {counts.failed} thất bại · {counts.canceled} đã hủy · {counts.blocked} chưa rõ phí
              </p>
              <button type="button" onClick={clearTracking} className={styles.secondaryButton}>
                Dọn trạng thái
              </button>
            </section>
          ) : null}
        </section>
      ) : null}
    </section>
  );
}
