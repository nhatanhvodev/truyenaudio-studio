import { useEffect, useMemo, useState } from 'react';

import DraftControls from './DraftControls';

import styles from './TranslationEditor.module.css';

type Run = {
  id: string;
  sha256: string;
  status: string;
};

type Segment = {
  id: string;
  sourceSegmentId: string;
  sourceText: string;
  targetText: string;
};

type Issue = {
  id: string;
  category: string;
  severity: string;
  status: string;
  evidence?: string | null;
  suggestion?: string | null;
  sourceSegmentId?: string | null;
};

type TranslationPayload = {
  run: Run;
  segments: Segment[];
  issues: Issue[];
  sourceRevisionId?: string | null;
};

type Props = {
  chapterId: string;
};

export default function TranslationEditor({ chapterId }: Props) {
  const [data, setData] = useState<TranslationPayload | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [savingSegmentId, setSavingSegmentId] = useState<string | null>(null);
  const [severityFilter, setSeverityFilter] = useState('ALL');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setError('');
      const response = await fetch(`/api/chapters/${chapterId}/translation`);
      const payload = (await response.json()) as TranslationPayload;
      if (!cancelled) {
        setData(payload);
        setDrafts(Object.fromEntries(payload.segments.map((segment) => [segment.sourceSegmentId, segment.targetText])));
      }
    }

    void load().catch((reason: unknown) => {
      if (!cancelled) {
        setError(reason instanceof Error ? reason.message : 'LOAD_FAILED');
      }
    });
    return () => {
      cancelled = true;
    };
  }, [chapterId]);

  const filteredIssues = useMemo(() => {
    const issues = data?.issues ?? [];
    if (severityFilter === 'ALL') {
      return issues;
    }
    return issues.filter((issue) => issue.severity === severityFilter);
  }, [data, severityFilter]);

  async function save(segment: Segment) {
    if (!data) {
      return;
    }
    setSavingSegmentId(segment.sourceSegmentId);
    setMessage('');
    setError('');
    const response = await fetch(`/api/chapters/${chapterId}/translation/segments/${segment.sourceSegmentId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        runId: data.run.id,
        targetText: drafts[segment.sourceSegmentId] ?? '',
        expectedRunHash: data.run.sha256,
      }),
    });
    const payload = await response.json();
    setSavingSegmentId(null);
    if (!response.ok) {
      setError(String(payload.detail ?? 'SAVE_FAILED'));
      return;
    }
    const nextPayload = payload as TranslationPayload;
    setData(nextPayload);
    setDrafts(Object.fromEntries(nextPayload.segments.map((item) => [item.sourceSegmentId, item.targetText])));
    setMessage('Da luu ban sua');
  }

  if (!data && !error) {
    return <p role="status">Dang tai ban dich</p>;
  }

  return (
    <section className={styles.shell} aria-label="Translation editor">
      <header className={styles.header}>
        <div>
          <h2 className={styles.title}>Translation Review</h2>
          <p className={styles.meta}>
            Run <span>{data?.run.id}</span> · <span>{data?.run.status}</span>
          </p>
        </div>
        <label className={styles.filterLabel}>
          Loc loi
          <select
            aria-label="Loc loi"
            value={severityFilter}
            onChange={(event) => setSeverityFilter(event.target.value)}
            className={styles.select}
          >
            <option value="ALL">ALL</option>
            <option value="CRITICAL">CRITICAL</option>
            <option value="MAJOR">MAJOR</option>
            <option value="MINOR">MINOR</option>
            <option value="INFO">INFO</option>
          </select>
        </label>
      </header>

      {message ? <p role="status" className={styles.success}>{message}</p> : null}
      {error ? <p role="alert" className={styles.error}>{error}</p> : null}

      <DraftControls
        chapterId={chapterId}
        baseRevisionId={data?.sourceRevisionId ?? null}
        content={drafts}
        onRestore={(restored) =>
          setDrafts((current) => ({ ...current, ...restored }))
        }
      />

      <div className={styles.layout}>
        <div className={styles.segmentList}>
          {(data?.segments ?? []).map((segment) => (
            <article key={segment.sourceSegmentId} className={styles.segment}>
              <div className={styles.source}>{segment.sourceText}</div>
              <textarea
                aria-label={`Ban dich ${segment.sourceSegmentId}`}
                value={drafts[segment.sourceSegmentId] ?? ''}
                onChange={(event) =>
                  setDrafts((current) => ({
                    ...current,
                    [segment.sourceSegmentId]: event.target.value,
                  }))
                }
                className={styles.textarea}
              />
              <button
                type="button"
                onClick={() => void save(segment)}
                disabled={savingSegmentId === segment.sourceSegmentId}
                className={styles.button}
              >
                Luu ban sua
              </button>
            </article>
          ))}
        </div>

        <aside className={styles.issues} aria-label="QA issues">
          {filteredIssues.length === 0 ? <p className={styles.empty}>Khong co loi</p> : null}
          {filteredIssues.map((issue) => (
            <article key={issue.id} className={styles.issue}>
              <div className={styles.issueTop}>
                <span>{issue.severity}</span>
                <span>{issue.category}</span>
              </div>
              <p className={styles.issueText}>{issue.suggestion}</p>
              {issue.evidence ? <p className={styles.evidence}>{issue.evidence}</p> : null}
            </article>
          ))}
        </aside>
      </div>
    </section>
  );
}
