import { useState } from 'react';

type RenderedAudio = {
  masterArtifactId: string;
  masterSha256: string;
  reusedSegmentIds: string[];
  renderedSegmentIds: string[];
};

type AsrIssue = {
  id: string;
  category: string;
  severity: string;
  segmentId: string;
  evidence: string;
};

type Props = {
  chapterId: string;
  presetId: string;
  initialRendered?: RenderedAudio | null;
  asrIssues?: AsrIssue[];
};

export default function AudioReview({ chapterId, presetId, initialRendered = null, asrIssues = [] }: Props) {
  const [rendered, setRendered] = useState<RenderedAudio | null>(initialRendered);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  async function configureAndRender() {
    setBusy(true);
    setMessage('');
    setError('');
    try {
      await postJson(`/api/chapters/${chapterId}/audio/configure-single`, { presetId });
      const payload = (await postJson(`/api/chapters/${chapterId}/audio/render`, {})) as RenderedAudio;
      setRendered(payload);
      setMessage('Audio ready for review');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'AUDIO_RENDER_FAILED');
    } finally {
      setBusy(false);
    }
  }

  async function approve() {
    if (!rendered) {
      return;
    }
    setBusy(true);
    setMessage('');
    setError('');
    try {
      await postJson(`/api/chapters/${chapterId}/audio/approve`, {
        masterArtifactId: rendered.masterArtifactId,
        expectedSha256: rendered.masterSha256,
      });
      setMessage('Audio approved');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'AUDIO_APPROVAL_FAILED');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section style={styles.shell} aria-label="Audio review">
      <header style={styles.header}>
        <div>
          <h2 style={styles.title}>Audio Review</h2>
          <p style={styles.meta}>{rendered ? rendered.masterArtifactId : 'No master rendered'}</p>
        </div>
        <div style={styles.actions}>
          <button type="button" onClick={() => void configureAndRender()} disabled={busy} style={styles.button}>
            Render
          </button>
          <button type="button" onClick={() => void approve()} disabled={busy || !rendered} style={styles.button}>
            Approve
          </button>
        </div>
      </header>

      {message ? <p role="status" style={styles.success}>{message}</p> : null}
      {error ? <p role="alert" style={styles.error}>{error}</p> : null}

      <dl style={styles.metrics}>
        <div>
          <dt>Rendered</dt>
          <dd>{rendered?.renderedSegmentIds.length ?? 0}</dd>
        </div>
        <div>
          <dt>Reused</dt>
          <dd>{rendered?.reusedSegmentIds.length ?? 0}</dd>
        </div>
        <div>
          <dt>Checksum</dt>
          <dd>{rendered?.masterSha256.slice(0, 12) ?? '-'}</dd>
        </div>
      </dl>

      {asrIssues.length > 0 ? (
        <section aria-label="ASR advisory" style={styles.asrBox}>
          <h3 style={styles.asrTitle}>ASR advisory</h3>
          <p style={styles.meta}>Các issue này chỉ ưu tiên nghe lại, không tự approve hoặc tự sửa.</p>
          <ul style={styles.issueList}>
            {asrIssues.map((issue) => (
              <li key={issue.id}>
                <strong>{issue.category} · {issue.severity} · {issue.segmentId}</strong>
                <span>{issue.evidence}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </section>
  );
}

async function postJson(url: string, body: object) {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(String(payload.detail ?? 'REQUEST_FAILED'));
  }
  return payload;
}

const styles: Record<string, React.CSSProperties> = {
  shell: {
    boxSizing: 'border-box',
    padding: 24,
    color: '#17202a',
    background: '#f7f8fb',
    fontFamily: 'Inter, Segoe UI, Arial, sans-serif',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 16,
  },
  title: {
    margin: 0,
    fontSize: 24,
    letterSpacing: 0,
  },
  meta: {
    margin: '6px 0 0',
    color: '#52606d',
    overflowWrap: 'anywhere',
  },
  actions: {
    display: 'flex',
    gap: 8,
  },
  button: {
    minWidth: 96,
    padding: '9px 14px',
    border: 0,
    borderRadius: 6,
    background: '#155eef',
    color: '#ffffff',
    fontWeight: 800,
  },
  success: {
    color: '#166534',
    fontWeight: 700,
  },
  error: {
    color: '#9a3412',
    fontWeight: 700,
  },
  metrics: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
    gap: 12,
    margin: '18px 0 0',
  },
  asrBox: {
    marginTop: 18,
    padding: 14,
    border: '1px solid #f2c94c',
    borderRadius: 8,
    background: '#fff9db',
  },
  asrTitle: {
    margin: 0,
    fontSize: 18,
  },
  issueList: {
    display: 'grid',
    gap: 8,
    margin: '12px 0 0',
    paddingLeft: 20,
  },
};
