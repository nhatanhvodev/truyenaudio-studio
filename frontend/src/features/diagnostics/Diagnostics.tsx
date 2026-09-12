import { useEffect, useMemo, useState } from 'react';
import { apiBlob, apiJson } from '../../shared/api';
import { Button } from '../../shared/ui';

import styles from './Diagnostics.module.css';

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
    <section aria-label="Diagnostics" className={styles.shell}>
      <header className={styles.header}>
        <div>
          <h1 className={styles.title}>Diagnostics</h1>
          <p className={styles.meta}>{health ? `${health.status} at ${health.timestamp}` : 'Loading'}</p>
        </div>
        <Button variant="secondary" onClick={() => void refresh()}>
          Refresh
        </Button>
      </header>

      <section aria-label="Health components" className={styles.grid}>
        {components.map(([name, component]) => (
          <article key={name} className={styles.component}>
            <strong>{name}</strong>
            <span className={component.status === 'ok' ? styles.ok : styles.status}>{component.status}</span>
            {component.message ? <small className={styles.message}>{component.message}</small> : null}
          </article>
        ))}
      </section>

      <section aria-label="Diagnostic export" className={styles.exportBox}>
        <label className={styles.checkboxLabel}>
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
            className={styles.textarea}
          />
        ) : null}
        <Button variant="primary" onClick={() => void exportDiagnostics()} disabled={busy}>
          Export ZIP
        </Button>
        {downloadUrl ? (
          <a href={downloadUrl} download="truyenaudio-diagnostics.zip" className={styles.download}>
            Download diagnostics ZIP
          </a>
        ) : null}
      </section>

      {error ? <p role="alert" className={styles.error}>{error}</p> : null}
    </section>
  );
}
