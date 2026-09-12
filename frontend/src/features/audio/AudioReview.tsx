import { useState } from 'react';
import styles from './AudioReview.module.css';

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
    <section className={styles.shell} aria-label="Audio review">
      <header className={styles.header}>
        <div>
          <h2 className={styles.title}>Audio Review</h2>
          <p className={styles.meta}>{rendered ? rendered.masterArtifactId : 'No master rendered'}</p>
        </div>
        <div className={styles.actions}>
          <button type="button" onClick={() => void configureAndRender()} disabled={busy} className={styles.button}>
            Render
          </button>
          <button type="button" onClick={() => void approve()} disabled={busy || !rendered} className={styles.button}>
            Approve
          </button>
        </div>
      </header>

      {message ? <p role="status" className={styles.success}>{message}</p> : null}
      {error ? <p role="alert" className={styles.error}>{error}</p> : null}

      <dl className={styles.metrics}>
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
        <section aria-label="ASR advisory" className={styles.asrBox}>
          <h3 className={styles.asrTitle}>ASR advisory</h3>
          <p className={styles.meta}>Các issue này chỉ ưu tiên nghe lại, không tự approve hoặc tự sửa.</p>
          <ul className={styles.issueList}>
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
