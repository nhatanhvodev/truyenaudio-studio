export type ImportCandidate = {
  ordinal: number | null;
  title: string | null;
  text: string;
  sourcePath: string;
  warnings: string[];
};

type Props = {
  candidates: ImportCandidate[];
  onConfirm: (candidates: ImportCandidate[]) => void;
};

export default function ImportPreview({ candidates, onConfirm }: Props) {
  return (
    <section style={styles.shell} aria-label="Import preview">
      <header style={styles.header}>
        <div>
          <h2 style={styles.title}>Import Preview</h2>
          <p style={styles.meta}>{candidates.length} candidates ready for mapping review</p>
        </div>
        <button type="button" onClick={() => onConfirm(candidates)} disabled={candidates.length === 0} style={styles.button}>
          Confirm import mapping
        </button>
      </header>

      <div style={styles.list}>
        {candidates.map((candidate, index) => (
          <article key={`${candidate.sourcePath}-${index}`} style={styles.item}>
            <div style={styles.itemHeader}>
              <div>
                <h3 style={styles.itemTitle}>{candidate.title ?? 'Untitled chapter'}</h3>
                <p style={styles.source}>{candidate.sourcePath}</p>
              </div>
              <span style={styles.ordinal}>{candidate.ordinal ?? '-'}</span>
            </div>
            {candidate.warnings.length > 0 ? (
              <div style={styles.warnings} aria-label={`Warnings for ${candidate.sourcePath}`}>
                {candidate.warnings.map((warning) => (
                  <span key={warning} style={styles.warning}>
                    {warning}
                  </span>
                ))}
              </div>
            ) : null}
            <p style={styles.excerpt}>{candidate.text || 'No preview text'}</p>
          </article>
        ))}
      </div>
    </section>
  );
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
    justifyContent: 'space-between',
    alignItems: 'center',
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
    color: '#52606d',
  },
  button: {
    minWidth: 180,
    padding: '9px 14px',
    border: 0,
    borderRadius: 6,
    background: '#155eef',
    color: '#ffffff',
    fontWeight: 800,
  },
  list: {
    display: 'grid',
    gap: 12,
  },
  item: {
    display: 'grid',
    gap: 10,
    padding: 14,
    border: '1px solid #d9e1ea',
    borderRadius: 8,
    background: '#ffffff',
  },
  itemHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    gap: 12,
  },
  itemTitle: {
    margin: 0,
    fontSize: 18,
    letterSpacing: 0,
  },
  source: {
    margin: '4px 0 0',
    color: '#52606d',
    overflowWrap: 'anywhere',
  },
  ordinal: {
    minWidth: 36,
    height: 28,
    display: 'grid',
    placeItems: 'center',
    borderRadius: 6,
    background: '#e9eef6',
    fontWeight: 800,
  },
  warnings: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: 6,
  },
  warning: {
    padding: '4px 8px',
    borderRadius: 6,
    background: '#fff1d6',
    color: '#7a4b00',
    fontSize: 12,
    fontWeight: 800,
  },
  excerpt: {
    margin: 0,
    color: '#344054',
    lineHeight: 1.5,
    whiteSpace: 'pre-wrap',
  },
};
