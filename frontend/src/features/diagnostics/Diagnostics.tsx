import { useEffect, useMemo, useState } from 'react';
import { apiBlob, apiJson } from '../../shared/api';

type HealthSnapshot = {
  timestamp: string;
  status: string;
  components: Record<string, { status: string; message?: string } & Record<string, unknown>>;
};

export function Diagnostics() {
  const [health, setHealth] = useState<HealthSnapshot | null>(null);
  const [includeSample, setIncludeSample] = useState(false);
  const [sampleText, setSampleText] = useState('');
  const [downloadUrl, setDownloadUrl] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    void refresh();
    return () => {
      if (downloadUrl) {
        URL.revokeObjectURL(downloadUrl);
      }
    };
  }, []);

  async function refresh() {
    setError('');
    try {
      setHealth(await apiJson<HealthSnapshot>('/api/diagnostics/health'));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'DIAGNOSTICS_HEALTH_FAILED');
    }
  }

  async function exportDiagnostics() {
    setBusy(true);
    setError('');
    try {
      const blob = await apiBlob('/api/diagnostics/export', {
        method: 'POST',
        body: {
          includeSample,
          sampleText: includeSample ? sampleText : null,
        },
      });
      if (downloadUrl) {
        URL.revokeObjectURL(downloadUrl);
      }
      setDownloadUrl(URL.createObjectURL(blob));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'DIAGNOSTICS_EXPORT_FAILED');
    } finally {
      setBusy(false);
    }
  }

  const components = useMemo(() => Object.entries(health?.components ?? {}), [health]);

  return (
    <section aria-label="Diagnostics" style={styles.shell}>
      <header style={styles.header}>
        <div>
          <h1 style={styles.title}>Diagnostics</h1>
          <p style={styles.meta}>{health ? `${health.status} at ${health.timestamp}` : 'Loading'}</p>
        </div>
        <button type="button" onClick={() => void refresh()} style={styles.secondaryButton}>
          Refresh
        </button>
      </header>

      <section aria-label="Health components" style={styles.grid}>
        {components.map(([name, component]) => (
          <article key={name} style={styles.component}>
            <strong>{name}</strong>
            <span style={component.status === 'ok' ? styles.ok : styles.status}>{component.status}</span>
            {component.message ? <small style={styles.message}>{component.message}</small> : null}
          </article>
        ))}
      </section>

      <section aria-label="Diagnostic export" style={styles.exportBox}>
        <label style={styles.checkboxLabel}>
          <input
            type="checkbox"
            checked={includeSample}
            onChange={(event) => setIncludeSample(event.target.checked)}
          />
          Include selected sample
        </label>
        {includeSample ? (
          <textarea
            aria-label="Selected sample text"
            value={sampleText}
            onChange={(event) => setSampleText(event.target.value)}
            style={styles.textarea}
          />
        ) : null}
        <button type="button" onClick={() => void exportDiagnostics()} disabled={busy} style={styles.primaryButton}>
          Export ZIP
        </button>
        {downloadUrl ? (
          <a href={downloadUrl} download="truyenaudio-diagnostics.zip" style={styles.download}>
            Download diagnostics ZIP
          </a>
        ) : null}
      </section>

      {error ? <p role="alert" style={styles.error}>{error}</p> : null}
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
    alignItems: 'center',
    justifyContent: 'space-between',
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
  grid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
    gap: 10,
  },
  component: {
    display: 'grid',
    gap: 6,
    minHeight: 78,
    padding: 12,
    border: '1px solid #d7dde8',
    borderRadius: 8,
    background: '#f8fafc',
  },
  ok: {
    color: '#166534',
    fontWeight: 900,
  },
  status: {
    color: '#9a3412',
    fontWeight: 900,
  },
  message: {
    color: '#52606d',
    overflowWrap: 'anywhere',
  },
  exportBox: {
    display: 'grid',
    gap: 12,
    padding: 12,
    border: '1px solid #d7dde8',
    borderRadius: 8,
    background: '#fbfcfe',
  },
  checkboxLabel: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    fontWeight: 800,
  },
  textarea: {
    minHeight: 110,
    boxSizing: 'border-box',
    padding: 10,
    border: '1px solid #c9d3df',
    borderRadius: 6,
    font: 'inherit',
    lineHeight: 1.5,
  },
  primaryButton: {
    justifySelf: 'start',
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
  download: {
    color: '#0b5cad',
    fontWeight: 900,
  },
  error: {
    margin: 0,
    color: '#9a3412',
    fontWeight: 900,
  },
};
