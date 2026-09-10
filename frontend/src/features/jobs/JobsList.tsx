import { Link } from 'react-router-dom';

import { retryableFailures, useJobEvents, type JobEvent, type JobEventStore } from './jobStore';

const STATUS_ORDER = ['RUNNING', 'CANCEL_REQUESTED', 'QUEUED', 'FAILED', 'BILLING_UNKNOWN', 'SUCCEEDED', 'CANCELED'];

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

/** Latest event per job: the list shows one row per job, not one per event. */
export function latestByJob(events: JobEvent[]): JobEvent[] {
  const latest = new Map<string, JobEvent>();
  for (const event of events) {
    latest.set(event.jobId, event);
  }
  return [...latest.values()].sort(
    (left, right) => STATUS_ORDER.indexOf(left.status) - STATUS_ORDER.indexOf(right.status),
  );
}

type Props = {
  /** Called for a retryable failure; the screen decides how to re-enqueue. */
  onRetry?: (event: JobEvent) => void;
  onCancel?: (event: JobEvent) => void;
  /** Shared store (defaults to the app-wide one; injectable for tests). */
  store?: JobEventStore;
};

/**
 * U07 round 2: jobs list bound to the shared store.
 *
 * One row per job (the latest event wins, so a job cannot appear twice), the
 * stream state, retry offered **only** for failures the backend would accept
 * (`retryableFailures`), cancel clearly marked while it is being honoured, and a
 * link to the draft view for a running translation.
 */
export default function JobsList({ onRetry, onCancel, store }: Props) {
  const { events, state, truncated } = useJobEvents(store);
  const jobs = latestByJob(events);
  const retryable = new Set(retryableFailures(events).map((event) => event.jobId));

  return (
    <section aria-label="Danh sách job" style={styles.shell}>
      <header style={styles.header}>
        <h2 style={styles.title}>Job</h2>
        <span style={styles.state} aria-label="Trạng thái luồng">
          {STATE_LABEL[state] ?? state}
        </span>
      </header>

      {truncated ? (
        <p role="status" style={styles.note}>
          Chỉ giữ 1.000 sự kiện gần nhất; job cũ hơn không còn trong danh sách.
        </p>
      ) : null}

      {jobs.length === 0 ? <p style={styles.note}>Chưa có job nào.</p> : null}

      {jobs.length > 0 ? (
        <table style={styles.table}>
          <thead>
            <tr>
              <th style={styles.th}>Job</th>
              <th style={styles.th}>Trạng thái</th>
              <th style={styles.th}>Tiến độ</th>
              <th style={styles.th}>Hành động</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => (
              <tr key={job.jobId}>
                <td style={styles.td}>
                  <Link to={`/jobs/${job.jobId}/draft`} style={styles.link}>
                    {job.jobId}
                  </Link>
                </td>
                <td style={styles.td}>
                  {STATUS_LABEL[job.status] ?? job.status}
                  {job.errorCode ? <span style={styles.error}> · {job.errorCode}</span> : null}
                </td>
                <td style={styles.td}>
                  {job.total > 0 ? `${job.current}/${job.total}` : '—'}
                </td>
                <td style={styles.td}>
                  {job.status === 'RUNNING' || job.status === 'QUEUED' ? (
                    <button type="button" onClick={() => onCancel?.(job)} style={styles.secondary}>
                      Hủy job
                    </button>
                  ) : null}
                  {retryable.has(job.jobId) ? (
                    <button type="button" onClick={() => onRetry?.(job)} style={styles.primary}>
                      Thử lại
                    </button>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </section>
  );
}

const styles: Record<string, React.CSSProperties> = {
  shell: { display: 'grid', gap: 10, padding: 16, border: '1px solid #d9e1ea', borderRadius: 8, background: '#ffffff' },
  header: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 },
  title: { margin: 0, fontSize: 18 },
  state: { fontSize: 13, fontWeight: 700, color: '#475467' },
  note: { margin: 0, fontSize: 13, color: '#667085' },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: 13 },
  th: { textAlign: 'left', padding: '6px 8px', borderBottom: '1px solid #e2e8f0', color: '#475467' },
  td: { padding: '6px 8px', borderBottom: '1px solid #f1f5f9', verticalAlign: 'middle' },
  link: { color: '#155eef', fontWeight: 700, textDecoration: 'none' },
  error: { color: '#9a3412', fontWeight: 700 },
  primary: { padding: '6px 10px', border: 0, borderRadius: 6, background: '#155eef', color: '#ffffff', fontWeight: 700 },
  secondary: { padding: '6px 10px', marginRight: 6, border: '1px solid #c8d1dc', borderRadius: 6, background: '#ffffff', fontWeight: 700 },
};
