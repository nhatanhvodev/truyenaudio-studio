import { useEffect, useMemo, useState } from 'react';

import { apiJson } from '../../shared/api';
import { retryableFailures, useJobEvents, type JobEvent, type JobEventStore } from '../jobs/jobStore';

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
    <section style={styles.shell} aria-label="Batch queue">
      <header style={styles.header}>
        <div>
          <h1 style={styles.title}>Batch queue</h1>
          <p style={styles.meta}>{selected.size}/{maxSelection} selected</p>
        </div>
        <button
          type="button"
          onClick={() => void queueTranslation()}
          disabled={busy || selected.size === 0}
          style={styles.primaryButton}
        >
          Queue translate
        </button>
      </header>

      <section style={styles.guardBox} aria-label="Cloud batch authorization">
        <label style={styles.label}>
          Provider profile ID
          <input value={providerProfileId} onChange={(event) => setProviderProfileId(event.target.value)} style={styles.input} />
        </label>
        <label style={styles.label}>
          Cloud consent ID
          <input value={cloudConsentId} onChange={(event) => setCloudConsentId(event.target.value)} style={styles.input} />
        </label>
        <label style={styles.label}>
          Batch operation ID
          <input value={quoteId} onChange={(event) => setQuoteId(event.target.value)} style={styles.input} />
        </label>
        <label style={styles.label}>
          Budget authorization ID
          <input value={budgetAuthorizationId} onChange={(event) => setBudgetAuthorizationId(event.target.value)} style={styles.input} />
        </label>
        <label style={styles.label}>
          Estimated input tokens
          <input value={estimatedUnits} onChange={(event) => setEstimatedUnits(event.target.value)} inputMode="numeric" style={styles.input} />
        </label>
      </section>

      <section style={styles.filterBox} aria-label="Bộ lọc chương">
        <label style={styles.label}>
          Tìm chương (lọc ở server)
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Tên hoặc tiêu đề chương"
            style={styles.input}
          />
        </label>
        <label style={styles.label}>
          Trạng thái
          <select
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value)}
            style={styles.input}
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

      <p style={styles.meta} role="status">
        {filterQuery ? 'Đang lọc ở server' : 'Không lọc'} · {items.length} chương đã tải
      </p>

      <div style={styles.list}>
        {items.map((chapter) => (
          <label key={chapter.id} style={styles.row}>
            <input
              type="checkbox"
              checked={selected.has(chapter.id)}
              onChange={() => toggle(chapter.id)}
              style={styles.checkbox}
            />
            <span style={styles.ordinal}>{chapter.ordinal}</span>
            <span style={styles.name}>{chapter.sourceTitle ?? chapter.translatedTitle ?? 'Untitled chapter'}</span>
            <span style={styles.state}>{chapter.state}</span>
            <span style={styles.progress}>{chapter.progress.current}/{chapter.progress.total}</span>
            <span style={styles.hash}>{chapter.hashes.sourceSha256?.slice(0, 10) ?? '-'}</span>
          </label>
        ))}
      </div>

      {nextCursor ? (
        <button type="button" onClick={() => void loadMore()} disabled={loading} style={styles.secondaryButton}>
          Load more
        </button>
      ) : null}
      {batch ? <p role="status" style={styles.success}>Queued {batch.total} jobs from {batch.batchId.slice(0, 12)}</p> : null}
      {error ? <p role="alert" style={styles.error}>{error}</p> : null}
      {loading && items.length === 0 ? <p style={styles.meta}>Loading</p> : null}

      {batch ? (
        <section aria-label="Tiến độ batch" style={styles.trackBox}>
          <header style={styles.trackHeader}>
            <h2 style={styles.trackTitle}>Tiến độ batch</h2>
            <span aria-label="Trạng thái luồng batch">{STATE_LABEL[state] ?? state}</span>
          </header>
          {truncated ? (
            <p role="status" style={styles.trackNote}>Chỉ giữ 1.000 sự kiện gần nhất; job cũ hơn không còn trong danh sách.</p>
          ) : null}
          {pendingCount > 0 && !finished ? (
            <p role="status" style={styles.trackNote}>Chờ sự kiện cho {pendingCount} job…</p>
          ) : null}

          <table style={styles.trackTable}>
            <thead>
              <tr>
                <th style={styles.trackTh}>Job</th>
                <th style={styles.trackTh}>Trạng thái</th>
                <th style={styles.trackTh}>Tiến độ</th>
                <th style={styles.trackTh}>Hành động</th>
              </tr>
            </thead>
            <tbody>
              {tracked.map(({ jobId, event }) => (
                <tr key={jobId}>
                  <td style={styles.trackTd} title={jobId}>{jobId.slice(0, 12)}</td>
                  <td style={styles.trackTd}>
                    {event ? STATUS_LABEL[event.status] ?? event.status : 'Chưa có sự kiện'}
                    {event?.errorCode ? <span style={styles.trackError}> · {event.errorCode}</span> : null}
                  </td>
                  <td style={styles.trackTd}>{event && event.total > 0 ? `${event.current}/${event.total}` : '—'}</td>
                  <td style={styles.trackTd}>
                    {event && (event.status === 'QUEUED' || event.status === 'RUNNING') ? (
                      <button type="button" onClick={() => handleCancel(event)} style={styles.secondaryButton}>
                        Hủy
                      </button>
                    ) : null}
                    {event && retryable.has(jobId) ? (
                      <button type="button" onClick={() => handleRetry(event)} style={styles.retryButton}>
                        Thử lại
                      </button>
                    ) : null}
                    {event?.status === 'FAILED' && !retryable.has(jobId) ? (
                      <span style={styles.trackNote}>không thể tự thử lại — hãy mở nháp để xử lý thủ công</span>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {failedRows.length > 0 && batch ? (
            <div role="alert" style={styles.alert}>
              <p style={styles.alertTitle}>{failedRows.length}/{batch.jobIds.length} job thất bại</p>
              <ul style={styles.alertList}>
                {failedRows.map(({ jobId, event }) => (
                  <li key={jobId}>
                    <span title={jobId}>{jobId.slice(0, 12)}</span>
                    {event?.errorCode ? <span style={styles.trackError}> · {event.errorCode}</span> : null}
                    {!retryable.has(jobId) ? <span style={styles.trackNote}> — không thể tự thử lại, hãy mở nháp để xử lý thủ công</span> : null}
                  </li>
                ))}
              </ul>
              {retryableFailedRows.length > 0 ? (
                <button type="button" onClick={retryAllRetryable} style={styles.retryButton}>
                  Thử lại tất cả lỗi retryable
                </button>
              ) : null}
            </div>
          ) : null}

          {finished ? (
            <section aria-label="Kết quả batch" style={styles.summaryBox}>
              <p style={styles.summaryLine}>
                Hoàn tất: {counts.succeeded}/{batch.jobIds.length} thành công · {counts.failed} thất bại · {counts.canceled} đã hủy · {counts.blocked} chưa rõ phí
              </p>
              <button type="button" onClick={clearTracking} style={styles.secondaryButton}>
                Dọn trạng thái
              </button>
            </section>
          ) : null}
        </section>
      ) : null}
    </section>
  );
}

const styles: Record<string, React.CSSProperties> = {
  shell: {
    display: 'grid',
    gap: 16,
    maxWidth: 920,
    margin: '0 auto',
    padding: 20,
    border: '1px solid #d7dde8',
    borderRadius: 8,
    background: '#ffffff',
  },
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    gap: 16,
  },
  title: {
    margin: 0,
    fontSize: 26,
    letterSpacing: 0,
  },
  meta: {
    margin: '4px 0 0',
    color: '#52606d',
    fontWeight: 700,
  },
  list: {
    display: 'grid',
    maxHeight: 520,
    overflow: 'auto',
    border: '1px solid #d7dde8',
    borderRadius: 8,
  },
  filterBox: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
    gap: 12,
    padding: 12,
    border: '1px solid #d7dde8',
    borderRadius: 8,
    background: '#ffffff',
  },
  guardBox: {
    display: 'grid',
    gridTemplateColumns: 'repeat(2, minmax(180px, 1fr))',
    gap: 12,
    padding: 12,
    border: '1px solid #d7dde8',
    borderRadius: 8,
    background: '#f8fafc',
  },
  label: {
    display: 'grid',
    gap: 6,
    color: '#344054',
    fontWeight: 800,
  },
  input: {
    width: '100%',
    minHeight: 38,
    boxSizing: 'border-box',
    padding: '8px 10px',
    border: '1px solid #c9d3df',
    borderRadius: 6,
    font: 'inherit',
  },
  row: {
    display: 'grid',
    gridTemplateColumns: '32px 56px minmax(160px, 1fr) 150px 72px 96px',
    gap: 10,
    alignItems: 'center',
    minHeight: 48,
    padding: '8px 10px',
    borderBottom: '1px solid #eef2f6',
  },
  checkbox: {
    width: 18,
    height: 18,
  },
  ordinal: {
    fontWeight: 900,
  },
  name: {
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  state: {
    color: '#344054',
    fontWeight: 800,
  },
  progress: {
    color: '#475467',
    fontVariantNumeric: 'tabular-nums',
  },
  hash: {
    color: '#667085',
    fontFamily: 'Consolas, monospace',
    fontSize: 12,
  },
  primaryButton: {
    padding: '10px 14px',
    border: 0,
    borderRadius: 6,
    background: '#155eef',
    color: '#ffffff',
    fontWeight: 900,
  },
  secondaryButton: {
    padding: '9px 12px',
    border: '1px solid #a9b7c6',
    borderRadius: 6,
    background: '#ffffff',
    color: '#17324d',
    fontWeight: 900,
  },
  retryButton: {
    padding: '9px 12px',
    border: 0,
    borderRadius: 6,
    background: '#155eef',
    color: '#ffffff',
    fontWeight: 900,
  },
  success: {
    margin: 0,
    color: '#166534',
    fontWeight: 900,
  },
  error: {
    margin: 0,
    color: '#9a3412',
    fontWeight: 900,
  },
  trackBox: {
    display: 'grid',
    gap: 10,
    padding: 12,
    border: '1px solid #d7dde8',
    borderRadius: 8,
    background: '#fbfdff',
  },
  trackHeader: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 8,
  },
  trackTitle: {
    margin: 0,
    fontSize: 18,
  },
  trackNote: {
    margin: 0,
    fontSize: 13,
    color: '#667085',
  },
  trackTable: {
    width: '100%',
    borderCollapse: 'collapse',
    fontSize: 13,
  },
  trackTh: {
    textAlign: 'left',
    padding: '6px 8px',
    borderBottom: '1px solid #e2e8f0',
    color: '#475467',
  },
  trackTd: {
    padding: '6px 8px',
    borderBottom: '1px solid #f1f5f9',
    verticalAlign: 'middle',
  },
  trackError: {
    color: '#9a3412',
    fontWeight: 700,
  },
  alert: {
    display: 'grid',
    gap: 8,
    padding: 12,
    border: '1px solid #f0c9a8',
    borderRadius: 8,
    background: '#fff7ed',
  },
  alertTitle: {
    margin: 0,
    color: '#9a3412',
    fontWeight: 900,
  },
  alertList: {
    margin: 0,
    paddingLeft: 18,
    color: '#17324d',
  },
  summaryBox: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 12,
    padding: 12,
    border: '1px solid #cfe3d2',
    borderRadius: 8,
    background: '#f2fbf4',
  },
  summaryLine: {
    margin: 0,
    color: '#166534',
    fontWeight: 900,
  },
};
