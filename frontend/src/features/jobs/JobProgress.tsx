import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import styles from './JobProgress.module.css';
import { useJobEvents, type JobEvent } from './jobStore';
import { useWenkuCrawl } from '../import/WenkuCrawlContext';

type Props = {
  events?: JobEvent[];
};

const STATE_LABEL: Record<string, string> = {
  offline: 'Ngoại tuyến',
  connecting: 'Đang kết nối',
  connected: 'Trực tuyến',
};

/** Statuses that need a distinct label in a small overlay. */
const STATUS_LABEL: Record<string, string> = {
  QUEUED: 'Đang chờ',
  RUNNING: 'Đang chạy',
  CANCEL_REQUESTED: 'Đang hủy…',
  CANCELED: 'Đã hủy',
  SUCCEEDED: 'Hoàn tất',
  FAILED: 'Lỗi',
  BILLING_UNKNOWN: 'Chưa rõ phí',
};

/**
 * Stream state as tokens. The colour never carries the state alone — the badge
 * always spells it out with `STATE_LABEL` next to the dot.
 */
const STATE_CLASS: Record<string, { dot: string; badge: string }> = {
  offline: { dot: styles.dotOffline, badge: styles.badgeOffline },
  connecting: { dot: styles.dotConnecting, badge: styles.badgeConnecting },
  connected: { dot: styles.dotConnected, badge: styles.badgeConnected },
};

/** Job status as tokens, always beside the Vietnamese `STATUS_LABEL`. */
const STATUS_CLASS: Record<string, string> = {
  QUEUED: styles.stateQueued,
  RUNNING: styles.stateRunning,
  CANCEL_REQUESTED: styles.stateRunning,
  CANCELED: styles.stateCanceled,
  SUCCEEDED: styles.stateDone,
  FAILED: styles.stateFailed,
  BILLING_UNKNOWN: styles.stateBlocked,
};

export function JobProgress({ events = [] }: Props) {
  // U07 round 2: the shared store owns the single SSE subscription, so two
  // consumers (overlay + jobs screen) never open two connections.
  const { events: streamEvents, state: streamState, truncated } = useJobEvents();
  const [collapsed, setCollapsed] = useState(false);

  const { crawlState } = useWenkuCrawl();

  const visibleEvents = useMemo(() => [...events, ...streamEvents].slice(-6), [events, streamEvents]);

  const stateClass = STATE_CLASS[streamState] ?? STATE_CLASS.offline;
  const hasContent = visibleEvents.length > 0 || crawlState?.crawling;

  // Auto-expand when something is happening
  useEffect(() => {
    if (hasContent) setCollapsed(false);
  }, [hasContent]);

  return (
    <aside aria-label="Jobs overlay" className={styles.overlay}>
      {/* Header */}
      <button
        type="button"
        className={styles.header}
        onClick={() => setCollapsed((c) => !c)}
        aria-expanded={!collapsed}
      >
        <span className={styles.headerLeft}>
          <span className={`${styles.dot} ${stateClass.dot}`} />
          <h2 className={styles.title}>Jobs</h2>
        </span>
        <span className={`${styles.badge} ${stateClass.badge}`}>
          {STATE_LABEL[streamState] ?? streamState}
        </span>
        <span className={styles.chevron}>{collapsed ? '▲' : '▼'}</span>
      </button>

      {/* Body */}
      {!collapsed ? (
        <div className={styles.body}>
          {/* Wenku crawl row */}
          {crawlState?.crawling ? (
            <article className={styles.crawlRow}>
              <div className={styles.jobTop}>
                <span className={styles.crawlTitle}>
                  <span>🔄</span>
                  Đang cào Wenku
                </span>
                <strong className={styles.crawlStatus}>RUNNING</strong>
              </div>
              <p className={styles.crawlBook} title={crawlState.bookTitle}>
                {crawlState.bookTitle}
              </p>
              <p className={styles.crawlChapters}>
                Chương {crawlState.startChapter} – {crawlState.endChapter}
              </p>
              <div className={styles.crawlBarTrack}>
                <div className={styles.crawlBarIndeterminate} />
              </div>
            </article>
          ) : null}

          {visibleEvents.length === 0 && !crawlState?.crawling ? (
            <p className={styles.empty}>Chưa có job đang chạy</p>
          ) : null}

          {truncated ? (
            <p className={styles.empty} role="status">
              Đã lược bớt sự kiện cũ (giữ 1.000 mục gần nhất).
            </p>
          ) : null}

          {visibleEvents.map((event) => (
            <article key={`${event.sequenceId}-${event.jobId}`} className={styles.job}>
              <div className={styles.jobTop}>
                <Link to={`/jobs/${event.jobId}/draft`} className={`${styles.jobId} ${styles.jobLink}`}>
                  {event.jobId}
                </Link>
                <strong className={`${styles.state} ${STATUS_CLASS[event.status] ?? styles.stateQueued}`}>
                  {STATUS_LABEL[event.status] ?? event.status}
                </strong>
              </div>
              <progress className={styles.progress} value={event.current} max={Math.max(event.total, 1)} />
              {event.status === 'CANCEL_REQUESTED' ? (
                <p className={styles.cancelNote}>Yêu cầu hủy đã gửi, job dừng ở ranh giới an toàn.</p>
              ) : null}
              {event.errorCode ? <p className={styles.error}>{event.errorCode}</p> : null}
            </article>
          ))}
        </div>
      ) : null}
    </aside>
  );
}
