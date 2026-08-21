import { useEffect, useMemo, useState } from 'react';

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
    <section style={styles.shell} aria-label="Translation editor">
      <header style={styles.header}>
        <div>
          <h2 style={styles.title}>Translation Review</h2>
          <p style={styles.meta}>
            Run <span>{data?.run.id}</span> · <span>{data?.run.status}</span>
          </p>
        </div>
        <label style={styles.filterLabel}>
          Loc loi
          <select
            aria-label="Loc loi"
            value={severityFilter}
            onChange={(event) => setSeverityFilter(event.target.value)}
            style={styles.select}
          >
            <option value="ALL">ALL</option>
            <option value="CRITICAL">CRITICAL</option>
            <option value="MAJOR">MAJOR</option>
            <option value="MINOR">MINOR</option>
            <option value="INFO">INFO</option>
          </select>
        </label>
      </header>

      {message ? <p role="status" style={styles.success}>{message}</p> : null}
      {error ? <p role="alert" style={styles.error}>{error}</p> : null}

      <div style={styles.layout}>
        <div style={styles.segmentList}>
          {(data?.segments ?? []).map((segment) => (
            <article key={segment.sourceSegmentId} style={styles.segment}>
              <div style={styles.source}>{segment.sourceText}</div>
              <textarea
                aria-label={`Ban dich ${segment.sourceSegmentId}`}
                value={drafts[segment.sourceSegmentId] ?? ''}
                onChange={(event) =>
                  setDrafts((current) => ({
                    ...current,
                    [segment.sourceSegmentId]: event.target.value,
                  }))
                }
                style={styles.textarea}
              />
              <button
                type="button"
                onClick={() => void save(segment)}
                disabled={savingSegmentId === segment.sourceSegmentId}
                style={styles.button}
              >
                Luu ban sua
              </button>
            </article>
          ))}
        </div>

        <aside style={styles.issues} aria-label="QA issues">
          {filteredIssues.length === 0 ? <p style={styles.empty}>Khong co loi</p> : null}
          {filteredIssues.map((issue) => (
            <article key={issue.id} style={styles.issue}>
              <div style={styles.issueTop}>
                <span>{issue.severity}</span>
                <span>{issue.category}</span>
              </div>
              <p style={styles.issueText}>{issue.suggestion}</p>
              {issue.evidence ? <p style={styles.evidence}>{issue.evidence}</p> : null}
            </article>
          ))}
        </aside>
      </div>
    </section>
  );
}

const styles: Record<string, React.CSSProperties> = {
  shell: {
    boxSizing: 'border-box',
    minHeight: '100vh',
    padding: 24,
    color: '#18212f',
    background: '#f7f8fb',
    fontFamily: 'Inter, Segoe UI, Arial, sans-serif',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 16,
    marginBottom: 16,
  },
  title: {
    margin: 0,
    fontSize: 24,
    letterSpacing: 0,
  },
  meta: {
    margin: '6px 0 0',
    color: '#586274',
  },
  filterLabel: {
    display: 'grid',
    gap: 6,
    fontWeight: 700,
  },
  select: {
    minWidth: 132,
    padding: '8px 10px',
    border: '1px solid #ccd4df',
    borderRadius: 6,
    background: '#ffffff',
  },
  success: {
    color: '#166534',
    fontWeight: 700,
  },
  error: {
    color: '#9a3412',
    fontWeight: 700,
  },
  layout: {
    display: 'grid',
    gridTemplateColumns: 'minmax(0, 1fr) 320px',
    gap: 16,
    alignItems: 'start',
  },
  segmentList: {
    display: 'grid',
    gap: 12,
  },
  segment: {
    display: 'grid',
    gap: 10,
    padding: 14,
    border: '1px solid #d9e1ea',
    borderRadius: 8,
    background: '#ffffff',
  },
  source: {
    whiteSpace: 'pre-wrap',
    lineHeight: 1.6,
    color: '#344054',
  },
  textarea: {
    width: '100%',
    minHeight: 144,
    resize: 'vertical',
    boxSizing: 'border-box',
    padding: 12,
    border: '1px solid #c8d1dc',
    borderRadius: 6,
    font: 'inherit',
    lineHeight: 1.5,
  },
  button: {
    justifySelf: 'start',
    padding: '9px 14px',
    border: 0,
    borderRadius: 6,
    background: '#155eef',
    color: '#ffffff',
    fontWeight: 800,
  },
  issues: {
    display: 'grid',
    gap: 10,
  },
  empty: {
    margin: 0,
    color: '#586274',
  },
  issue: {
    padding: 12,
    border: '1px solid #d9e1ea',
    borderRadius: 8,
    background: '#ffffff',
  },
  issueTop: {
    display: 'flex',
    justifyContent: 'space-between',
    gap: 8,
    fontSize: 12,
    fontWeight: 800,
    color: '#475467',
  },
  issueText: {
    margin: '8px 0 0',
    lineHeight: 1.4,
  },
  evidence: {
    margin: '8px 0 0',
    color: '#7a4b00',
    fontSize: 13,
  },
};
