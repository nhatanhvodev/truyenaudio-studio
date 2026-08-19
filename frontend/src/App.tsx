import { useEffect, useMemo, useState } from 'react';

type ComponentStatus = {
  status: string;
  message: string;
};

type HealthResponse = {
  status: string;
  components: Record<string, ComponentStatus>;
};

type PocResponse = {
  status: string;
  gates?: {
    overall?: {
      passed?: boolean;
    };
  };
};

type LoadState = {
  loading: boolean;
  health?: HealthResponse;
  poc?: PocResponse;
  offline: boolean;
};

const cards = [
  { key: 'api', label: 'API' },
  { key: 'worker', label: 'Worker' },
  { key: 'sqlite', label: 'SQLite' },
  { key: 'ffmpeg', label: 'FFmpeg' },
];

export default function App() {
  const [state, setState] = useState<LoadState>({ loading: true, offline: false });

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const [healthResponse, pocResponse] = await Promise.all([
          fetch('/api/health/ready', { cache: 'no-store' }),
          fetch('/api/poc/status', { cache: 'no-store' }),
        ]);
        const health = (await healthResponse.json()) as HealthResponse;
        const poc = (await pocResponse.json()) as PocResponse;
        if (!cancelled) {
          setState({ loading: false, health, poc, offline: false });
        }
      } catch {
        if (!cancelled) {
          setState({ loading: false, offline: true });
        }
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  const summary = useMemo(() => {
    if (state.loading) {
      return 'Đang tải trạng thái';
    }
    if (state.offline) {
      return 'API cần kiểm tra';
    }
    return state.health?.status === 'ready' ? 'Sẵn sàng local' : 'API cần kiểm tra';
  }, [state]);

  return (
    <main style={styles.shell}>
      <section style={styles.header}>
        <div>
          <h1 style={styles.title}>Truyện Audio Studio</h1>
          <p style={styles.subtitle}>Bảng điều khiển local cho POC dịch và audio.</p>
        </div>
        <a href="/diagnostics" style={styles.link}>
          Chẩn đoán
        </a>
      </section>

      <p role="status" aria-live="polite" style={styles.status}>
        {summary}
      </p>

      {state.offline ? <p style={styles.warning}>Mất kết nối local</p> : null}

      <section aria-label="Trang thai he thong" style={styles.grid}>
        {cards.map((card) => {
          const component = state.health?.components?.[card.key];
          return (
            <article key={card.key} style={styles.card}>
              <span style={styles.cardLabel}>{card.label}</span>
              <span style={statusStyle(component?.status)}>{component?.status ?? 'loading'}</span>
              <p style={styles.cardMessage}>{component?.message ?? 'dang kiem tra'}</p>
            </article>
          );
        })}
        <article style={styles.card}>
          <span style={styles.cardLabel}>POC Gate</span>
          <span style={statusStyle(state.poc?.gates?.overall?.passed ? 'ok' : state.poc?.status)}>
            {state.poc?.gates?.overall?.passed ? 'pass' : state.poc?.status ?? 'loading'}
          </span>
          <p style={styles.cardMessage}>Báo cáo fake offline và checksum trong data/projects/poc.</p>
        </article>
      </section>
    </main>
  );
}

function statusStyle(status: string | undefined): React.CSSProperties {
  const ok = status === 'ok' || status === 'ready' || status === 'pass';
  return {
    ...styles.badge,
    background: ok ? '#e8f5ec' : '#fff4d6',
    color: ok ? '#1c6638' : '#7a4b00',
  };
}

const styles: Record<string, React.CSSProperties> = {
  shell: {
    minHeight: '100vh',
    boxSizing: 'border-box',
    padding: 32,
    color: '#17202a',
    background: '#f5f7fa',
    fontFamily: 'Inter, Segoe UI, Arial, sans-serif',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 16,
    maxWidth: 960,
    margin: '0 auto 24px',
  },
  title: {
    margin: 0,
    fontSize: 34,
    letterSpacing: 0,
  },
  subtitle: {
    margin: '8px 0 0',
    color: '#52606d',
  },
  link: {
    color: '#0b5cad',
    textDecoration: 'none',
    fontWeight: 700,
  },
  status: {
    maxWidth: 960,
    margin: '0 auto 16px',
    fontWeight: 700,
  },
  warning: {
    maxWidth: 960,
    margin: '0 auto 16px',
    color: '#8a3a00',
  },
  grid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(168px, 1fr))',
    gap: 12,
    maxWidth: 960,
    margin: '0 auto',
  },
  card: {
    minHeight: 112,
    padding: 16,
    border: '1px solid #d9e1e8',
    borderRadius: 8,
    background: '#ffffff',
    boxSizing: 'border-box',
  },
  cardLabel: {
    display: 'block',
    marginBottom: 12,
    fontWeight: 800,
  },
  badge: {
    display: 'inline-block',
    minWidth: 72,
    padding: '5px 8px',
    borderRadius: 999,
    textAlign: 'center',
    fontSize: 13,
    fontWeight: 700,
  },
  cardMessage: {
    margin: '12px 0 0',
    color: '#52606d',
    lineHeight: 1.4,
  },
};
