import { Link } from 'react-router-dom';

import styles from './JobsList.module.css';
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
  onRetry?: (event: JobEvent) => void | Promise<void>;
  onCancel?: (event: JobEvent) => void | Promise<void>;
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
    <section aria-label="Danh sách job" className={styles.shell}>
      <header className={styles.header}>
        <h2 className={styles.title}>Job</h2>
        <span className={styles.streamState} aria-label="Trạng thái luồng">
          {STATE_LABEL[state] ?? state}
        </span>
      </header>

      {truncated ? (
        <p role="status" className={styles.note}>
          Chỉ giữ 1.000 sự kiện gần nhất; job cũ hơn không còn trong danh sách.
        </p>
      ) : null}

      {jobs.length === 0 ? <p className={styles.note}>Chưa có job nào.</p> : null}

      {jobs.length > 0 ? (
        <table className={styles.table}>
          <thead>
            <tr>
              <th className={styles.th}>Job</th>
              <th className={styles.th}>Trạng thái</th>
              <th className={styles.th}>Tiến độ</th>
              <th className={styles.th}>Hành động</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => (
              <tr key={job.jobId}>
                <td className={styles.td}>
                  <Link to={`/jobs/${job.jobId}/draft`} className={`${styles.jobId} ${styles.jobLink}`}>
                    {job.jobId}
                  </Link>
                </td>
                <td className={`${styles.td} ${styles.state} ${STATUS_CLASS[job.status] ?? styles.stateQueued}`}>
                  {STATUS_LABEL[job.status] ?? job.status}
                  {job.errorCode ? <span className={styles.error}> · {job.errorCode}</span> : null}
                </td>
                <td className={`${styles.td} ${styles.progress}`}>
                  {job.total > 0 ? `${job.current}/${job.total}` : '—'}
                </td>
                <td className={`${styles.td} ${styles.actions}`}>
                  {job.status === 'RUNNING' || job.status === 'QUEUED' ? (
                    <button type="button" onClick={() => void onCancel?.(job)} className={styles.secondary}>
                      Hủy job
                    </button>
                  ) : null}
                  {retryable.has(job.jobId) ? (
                    <button type="button" onClick={() => void onRetry?.(job)} className={styles.primary}>
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
