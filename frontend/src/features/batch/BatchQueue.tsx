import { useEffect, useMemo, useState } from 'react';
import { apiJson } from '../../shared/api';

type ChapterSummary = {
  id: string;
  ordinal: number;
  sourceTitle: string | null;
  translatedTitle: string | null;
  state: string;
  progress: {
    current: number;
    total: number;
  };
  cost: {
    estimatedVnd: number | null;
    actualVnd: number | null;
  };
  issues: number;
  hashes: {
    sourceSha256: string | null;
    translationSha256: string | null;
  };
};

type ChapterPage = {
  items: ChapterSummary[];
  nextCursor: string | null;
  total: number;
};

type BatchView = {
  batchId: string;
  total: number;
  queued: number;
  running: number;
  succeeded: number;
  failed: number;
  canceled: number;
  blocked: number;
  jobIds: string[];
};

type Props = {
  projectId: string;
};

const pageLimit = 25;
const maxSelection = 50;

export function BatchQueue({ projectId }: Props) {
  const [items, setItems] = useState<ChapterSummary[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [batch, setBatch] = useState<BatchView | null>(null);
  const [providerProfileId, setProviderProfileId] = useState('');
  const [cloudConsentId, setCloudConsentId] = useState('');
  const [quoteId, setQuoteId] = useState('');
  const [budgetAuthorizationId, setBudgetAuthorizationId] = useState('');
  const [estimatedUnits, setEstimatedUnits] = useState('');

  useEffect(() => {
    let cancelled = false;
    setItems([]);
    setNextCursor(null);
    setSelected(new Set());
    setBatch(null);
    setError('');
    setLoading(true);
    apiJson<ChapterPage>(`/api/projects/${projectId}/chapters?limit=${pageLimit}`)
      .then((page) => {
        if (!cancelled) {
          setItems(page.items);
          setNextCursor(page.nextCursor);
        }
      })
      .catch((reason) => {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : 'CHAPTER_PAGE_FAILED');
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  const selectedIds = useMemo(() => Array.from(selected), [selected]);

  async function loadMore() {
    if (!nextCursor) {
      return;
    }
    setLoading(true);
    setError('');
    try {
      const page = await apiJson<ChapterPage>(
        `/api/projects/${projectId}/chapters?limit=${pageLimit}&cursor=${encodeURIComponent(nextCursor)}`,
      );
      setItems((current) => [...current, ...page.items]);
      setNextCursor(page.nextCursor);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'CHAPTER_PAGE_FAILED');
    } finally {
      setLoading(false);
    }
  }

  function toggle(chapterId: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(chapterId)) {
        next.delete(chapterId);
        return next;
      }
      if (next.size < maxSelection) {
        next.add(chapterId);
      }
      return next;
    });
  }

  async function queueTranslation() {
    if (selectedIds.length === 0) {
      return;
    }
    const estimate = Number.parseInt(estimatedUnits, 10);
    if (
      !providerProfileId.trim()
      || !cloudConsentId.trim()
      || !quoteId.trim()
      || !budgetAuthorizationId.trim()
      || !Number.isFinite(estimate)
      || estimate <= 0
    ) {
      setError('BATCH_CLOUD_AUTHORIZATION_REQUIRED');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const payload = await apiJson<BatchView>('/api/batches', {
        method: 'POST',
        body: {
          projectId,
          chapterIds: selectedIds,
          stage: 'TRANSLATE',
          quoteId: quoteId.trim(),
          providerProfileId: providerProfileId.trim(),
          cloudConsentId: cloudConsentId.trim(),
          budgetAuthorizationId: budgetAuthorizationId.trim(),
          estimatedUnits: estimate,
          estimatedUnit: 'INPUT_TOKEN',
        },
      });
      setBatch(payload);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'BATCH_QUEUE_FAILED');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section style={styles.shell} aria-label="Batch queue">
      <header style={styles.header}>
        <div>
          <h1 style={styles.title}>Batch queue</h1>
          <p style={styles.meta}>{selected.size}/{maxSelection} selected</p>
        </div>
        <button
          type="button"
          onClick={() => void queueTranslation()}
          disabled={busy || selected.size === 0}
          style={styles.primaryButton}
        >
          Queue translate
        </button>
      </header>

      <section style={styles.guardBox} aria-label="Cloud batch authorization">
        <label style={styles.label}>
          Provider profile ID
          <input value={providerProfileId} onChange={(event) => setProviderProfileId(event.target.value)} style={styles.input} />
        </label>
        <label style={styles.label}>
          Cloud consent ID
          <input value={cloudConsentId} onChange={(event) => setCloudConsentId(event.target.value)} style={styles.input} />
        </label>
        <label style={styles.label}>
          Batch operation ID
          <input value={quoteId} onChange={(event) => setQuoteId(event.target.value)} style={styles.input} />
        </label>
        <label style={styles.label}>
          Budget authorization ID
          <input value={budgetAuthorizationId} onChange={(event) => setBudgetAuthorizationId(event.target.value)} style={styles.input} />
        </label>
        <label style={styles.label}>
          Estimated input tokens
          <input value={estimatedUnits} onChange={(event) => setEstimatedUnits(event.target.value)} inputMode="numeric" style={styles.input} />
        </label>
      </section>

      <div style={styles.list}>
        {items.map((chapter) => (
          <label key={chapter.id} style={styles.row}>
            <input
              type="checkbox"
              checked={selected.has(chapter.id)}
              onChange={() => toggle(chapter.id)}
              style={styles.checkbox}
            />
            <span style={styles.ordinal}>{chapter.ordinal}</span>
            <span style={styles.name}>{chapter.sourceTitle ?? chapter.translatedTitle ?? 'Untitled chapter'}</span>
            <span style={styles.state}>{chapter.state}</span>
            <span style={styles.progress}>{chapter.progress.current}/{chapter.progress.total}</span>
            <span style={styles.hash}>{chapter.hashes.sourceSha256?.slice(0, 10) ?? '-'}</span>
          </label>
        ))}
      </div>

      {nextCursor ? (
        <button type="button" onClick={() => void loadMore()} disabled={loading} style={styles.secondaryButton}>
          Load more
        </button>
      ) : null}
      {batch ? <p role="status" style={styles.success}>Queued {batch.total} jobs from {batch.batchId.slice(0, 12)}</p> : null}
      {error ? <p role="alert" style={styles.error}>{error}</p> : null}
      {loading && items.length === 0 ? <p style={styles.meta}>Loading</p> : null}
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
    justifyContent: 'space-between',
    alignItems: 'center',
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
  list: {
    display: 'grid',
    maxHeight: 520,
    overflow: 'auto',
    border: '1px solid #d7dde8',
    borderRadius: 8,
  },
  guardBox: {
    display: 'grid',
    gridTemplateColumns: 'repeat(2, minmax(180px, 1fr))',
    gap: 12,
    padding: 12,
    border: '1px solid #d7dde8',
    borderRadius: 8,
    background: '#f8fafc',
  },
  label: {
    display: 'grid',
    gap: 6,
    color: '#344054',
    fontWeight: 800,
  },
  input: {
    width: '100%',
    minHeight: 38,
    boxSizing: 'border-box',
    padding: '8px 10px',
    border: '1px solid #c9d3df',
    borderRadius: 6,
    font: 'inherit',
  },
  row: {
    display: 'grid',
    gridTemplateColumns: '32px 56px minmax(160px, 1fr) 150px 72px 96px',
    gap: 10,
    alignItems: 'center',
    minHeight: 48,
    padding: '8px 10px',
    borderBottom: '1px solid #eef2f6',
  },
  checkbox: {
    width: 18,
    height: 18,
  },
  ordinal: {
    fontWeight: 900,
  },
  name: {
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  state: {
    color: '#344054',
    fontWeight: 800,
  },
  progress: {
    color: '#475467',
    fontVariantNumeric: 'tabular-nums',
  },
  hash: {
    color: '#667085',
    fontFamily: 'Consolas, monospace',
    fontSize: 12,
  },
  primaryButton: {
    padding: '10px 14px',
    border: 0,
    borderRadius: 6,
    background: '#155eef',
    color: '#ffffff',
    fontWeight: 900,
  },
  secondaryButton: {
    justifySelf: 'start',
    padding: '9px 12px',
    border: '1px solid #a9b7c6',
    borderRadius: 6,
    background: '#ffffff',
    color: '#17324d',
    fontWeight: 900,
  },
  success: {
    margin: 0,
    color: '#166534',
    fontWeight: 900,
  },
  error: {
    margin: 0,
    color: '#9a3412',
    fontWeight: 900,
  },
};
