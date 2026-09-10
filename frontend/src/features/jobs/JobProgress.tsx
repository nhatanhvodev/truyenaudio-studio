import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

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

const STATE_COLOR: Record<string, { bg: string; text: string; dot: string }> = {
  offline:    { bg: '#fef2f2', text: '#b91c1c', dot: '#ef4444' },
  connecting: { bg: '#fffbeb', text: '#92400e', dot: '#f59e0b' },
  connected:  { bg: '#f0fdf4', text: '#166534', dot: '#22c55e' },
};

export function JobProgress({ events = [] }: Props) {
  // U07 round 2: the shared store owns the single SSE subscription, so two
  // consumers (overlay + jobs screen) never open two connections.
  const { events: streamEvents, state: streamState, truncated } = useJobEvents();
  const [collapsed, setCollapsed] = useState(false);

  const { crawlState } = useWenkuCrawl();

  const visibleEvents = useMemo(() => [...events, ...streamEvents].slice(-6), [events, streamEvents]);

  const stateStyle = STATE_COLOR[streamState] ?? STATE_COLOR.offline;
  const hasContent = visibleEvents.length > 0 || crawlState?.crawling;

  // Auto-expand when something is happening
  useEffect(() => {
    if (hasContent) setCollapsed(false);
  }, [hasContent]);

  return (
    <aside aria-label="Jobs overlay" style={styles.overlay}>
      {/* Header */}
      <button
        type="button"
        style={styles.header}
        onClick={() => setCollapsed((c) => !c)}
        aria-expanded={!collapsed}
      >
        <span style={styles.headerLeft}>
          <span style={{ ...styles.dot, background: stateStyle.dot }} />
          <h2 style={styles.title}>Jobs</h2>
        </span>
        <span style={{ ...styles.badge, background: stateStyle.bg, color: stateStyle.text }}>
          {STATE_LABEL[streamState] ?? streamState}
        </span>
        <span style={styles.chevron}>{collapsed ? '▲' : '▼'}</span>
      </button>

      {/* Body */}
      {!collapsed ? (
        <div style={styles.body}>
          {/* Wenku crawl row */}
          {crawlState?.crawling ? (
            <article style={styles.crawlRow}>
              <div style={styles.jobTop}>
                <span style={styles.crawlTitle}>
                  <span style={{ marginRight: 4 }}>🔄</span>
                  Đang cào Wenku
                </span>
                <strong style={styles.crawlStatus}>RUNNING</strong>
              </div>
              <p style={styles.crawlBook} title={crawlState.bookTitle}>
                {crawlState.bookTitle}
              </p>
              <p style={styles.crawlChapters}>
                Chương {crawlState.startChapter} – {crawlState.endChapter}
              </p>
              <div style={styles.crawlBarTrack}>
                <div style={styles.crawlBarIndeterminate} />
              </div>
            </article>
          ) : null}

          {visibleEvents.length === 0 && !crawlState?.crawling ? (
            <p style={styles.empty}>Chưa có job đang chạy</p>
          ) : null}

          {truncated ? (
            <p style={styles.empty} role="status">
              Đã lược bớt sự kiện cũ (giữ 1.000 mục gần nhất).
            </p>
          ) : null}

          {visibleEvents.map((event) => (
            <article key={`${event.sequenceId}-${event.jobId}`} style={styles.job}>
              <div style={styles.jobTop}>
                <Link to={`/jobs/${event.jobId}/draft`} style={styles.jobLink}>
                  {event.jobId}
                </Link>
                <strong>{STATUS_LABEL[event.status] ?? event.status}</strong>
              </div>
              <progress value={event.current} max={Math.max(event.total, 1)} style={styles.progress} />
              {event.status === 'CANCEL_REQUESTED' ? (
                <p style={styles.cancelNote}>Yêu cầu hủy đã gửi, job dừng ở ranh giới an toàn.</p>
              ) : null}
              {event.errorCode ? <p style={styles.error}>{event.errorCode}</p> : null}
            </article>
          ))}
        </div>
      ) : null}
    </aside>
  );
}

const styles: Record<string, React.CSSProperties> = {
  overlay: {
    position: 'fixed',
    right: 16,
    bottom: 16,
    zIndex: 10,
    width: 300,
    maxWidth: 'calc(100vw - 32px)',
    border: '1px solid #d7dde8',
    borderRadius: 10,
    background: '#ffffff',
    boxShadow: '0 8px 24px rgba(18,29,43,0.13)',
    color: '#17202a',
    overflow: 'hidden',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: '10px 12px',
    background: 'none',
    border: 'none',
    width: '100%',
    cursor: 'pointer',
    textAlign: 'left',
  },
  headerLeft: { display: 'flex', alignItems: 'center', gap: 6, flex: 1 },
  dot: {
    width: 8,
    height: 8,
    borderRadius: '50%',
    flexShrink: 0,
  },
  title: { margin: 0, fontSize: 14, fontWeight: 700, letterSpacing: 0 },
  badge: {
    padding: '3px 7px',
    borderRadius: 6,
    fontSize: 11,
    fontWeight: 700,
  },
  chevron: { fontSize: 10, color: '#94a3b8', flexShrink: 0 },
  body: { padding: '0 12px 12px' },
  empty: { margin: '8px 0 0', color: '#94a3b8', fontSize: 13, fontStyle: 'italic' },

  // Wenku crawl row
  crawlRow: {
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
    marginTop: 8,
    padding: 10,
    background: '#f0fdf4',
    border: '1px solid #bbf7d0',
    borderRadius: 6,
  },
  crawlTitle: { display: 'flex', alignItems: 'center', fontSize: 12, color: '#15803d', fontWeight: 700 },
  crawlStatus: { fontSize: 11, color: '#16a34a', background: '#dcfce7', padding: '1px 6px', borderRadius: 4 },
  crawlBook: {
    margin: 0, fontSize: 12, color: '#166534', fontWeight: 600,
    whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
  },
  crawlChapters: { margin: 0, fontSize: 11, color: '#4ade80' },
  crawlBarTrack: {
    height: 4, background: '#dcfce7', borderRadius: 99, overflow: 'hidden', position: 'relative',
  },
  crawlBarIndeterminate: {
    position: 'absolute',
    top: 0,
    left: '-40%',
    height: '100%',
    width: '40%',
    background: 'linear-gradient(90deg, transparent, #16a34a, transparent)',
    animation: 'wenku-shimmer 1.5s linear infinite',
  },

  // Regular jobs
  job: { display: 'grid', gap: 6, marginTop: 10 },
  jobTop: { display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: 12 },
  jobLink: { color: '#155eef', fontWeight: 700, textDecoration: 'none' },
  cancelNote: { margin: 0, fontSize: 12, color: '#92400e', fontWeight: 700 },
  progress: { width: '100%' },
  error: { margin: 0, color: '#9a3412', fontSize: 12 },
};
