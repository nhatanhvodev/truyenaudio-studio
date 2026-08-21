import { useEffect, useMemo, useRef, useState } from 'react';
import { apiJson } from '../../shared/api';

type JobEvent = {
  sequenceId: string;
  jobId: string;
  status: string;
  current: number;
  total: number;
  errorCode?: string | null;
};

type Props = {
  events?: JobEvent[];
};

export function JobProgress({ events = [] }: Props) {
  const [streamEvents, setStreamEvents] = useState<JobEvent[]>([]);
  const [streamState, setStreamState] = useState('offline');
  const lastSequenceRef = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let source: EventSource | null = null;

    async function connect() {
      try {
        const snapshot = await apiJson<{ events: JobEvent[] }>('/api/jobs/snapshot');
        if (cancelled) {
          return;
        }
        appendEvents(snapshot.events);
      } catch {
        if (cancelled) {
          return;
        }
        setStreamState('offline');
      }

      if (cancelled || typeof EventSource === 'undefined') {
        return;
      }
      const lastEventId = lastSequenceRef.current;
      const url = lastEventId ? `/api/jobs/events?after=${encodeURIComponent(lastEventId)}` : '/api/jobs/events';
      source = new EventSource(url);
      setStreamState('connecting');
      source.onopen = () => setStreamState('connected');
      const handleJobEvent = (message: MessageEvent) => {
        try {
          appendEvents([JSON.parse(message.data) as JobEvent]);
        } catch {
          setStreamState('offline');
        }
      };
      source.addEventListener('job', (message) => handleJobEvent(message as MessageEvent));
      source.onerror = () => setStreamState('offline');
    }

    function appendEvents(nextEvents: JobEvent[]) {
      if (nextEvents.length === 0) {
        return;
      }
      lastSequenceRef.current = nextEvents.at(-1)?.sequenceId ?? lastSequenceRef.current;
      setStreamEvents((current) => [...current, ...nextEvents].slice(-6));
    }

    void connect();

    return () => {
      cancelled = true;
      source?.close();
    };
  }, []);

  const visibleEvents = useMemo(() => [...events, ...streamEvents].slice(-6), [events, streamEvents]);

  return (
    <aside aria-label="Jobs overlay" style={styles.overlay}>
      <div style={styles.header}>
        <h2 style={styles.title}>Jobs</h2>
        <span style={styles.badge}>{streamState}</span>
      </div>
      {visibleEvents.length === 0 ? <p style={styles.empty}>Chưa có job đang chạy</p> : null}
      {visibleEvents.map((event) => (
        <article key={`${event.sequenceId}-${event.jobId}`} style={styles.job}>
          <div style={styles.jobTop}>
            <span>{event.jobId}</span>
            <strong>{event.status}</strong>
          </div>
          <progress value={event.current} max={Math.max(event.total, 1)} style={styles.progress} />
          {event.errorCode ? <p style={styles.error}>{event.errorCode}</p> : null}
        </article>
      ))}
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
    padding: 12,
    border: '1px solid #d7dde8',
    borderRadius: 8,
    background: '#ffffff',
    boxShadow: '0 8px 24px rgba(18, 29, 43, 0.12)',
    color: '#17202a',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 8,
  },
  title: {
    margin: 0,
    fontSize: 16,
    letterSpacing: 0,
  },
  badge: {
    padding: '4px 7px',
    borderRadius: 6,
    background: '#eef4ff',
    color: '#1849a9',
    fontSize: 12,
    fontWeight: 800,
  },
  empty: {
    margin: '10px 0 0',
    color: '#586274',
  },
  job: {
    display: 'grid',
    gap: 6,
    marginTop: 10,
  },
  jobTop: {
    display: 'flex',
    justifyContent: 'space-between',
    gap: 8,
    fontSize: 12,
  },
  progress: {
    width: '100%',
  },
  error: {
    margin: 0,
    color: '#9a3412',
    fontSize: 12,
  },
};
