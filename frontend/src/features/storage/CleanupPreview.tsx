export type CleanupCandidate = {
  candidateType: string;
  relativePath: string;
  byteSize: number;
  sha256: string;
  artifactId?: string | null;
};

export type CleanupPlan = {
  planId: string;
  snapshotHash: string;
  candidates: CleanupCandidate[];
  totalBytes: number;
};

type CleanupPreviewProps = {
  plan: CleanupPlan;
  onExecute: (planId: string, snapshotHash: string) => void;
};

export function CleanupPreview({ plan, onExecute }: CleanupPreviewProps) {
  const empty = plan.candidates.length === 0;

  return (
    <section aria-label="Cleanup preview" style={styles.shell}>
      <header style={styles.header}>
        <div>
          <h2 style={styles.title}>Storage cleanup</h2>
          <p style={styles.meta}>{formatBytes(plan.totalBytes)} reviewed for deletion</p>
        </div>
        <span style={empty ? styles.emptyBadge : styles.reviewBadge}>{empty ? 'No candidates' : 'Preview only'}</span>
      </header>

      {empty ? (
        <p style={styles.empty}>No reviewed cleanup candidates.</p>
      ) : (
        <ul style={styles.list}>
          {plan.candidates.map((candidate) => (
            <li key={`${candidate.candidateType}:${candidate.relativePath}`} style={styles.item}>
              <div>
                <strong>{candidate.relativePath}</strong>
                <span style={styles.type}>{candidate.candidateType}</span>
              </div>
              <span style={styles.bytes}>{formatBytes(candidate.byteSize)}</span>
            </li>
          ))}
        </ul>
      )}

      <button
        type="button"
        style={styles.dangerButton}
        disabled={empty}
        onClick={() => onExecute(plan.planId, plan.snapshotHash)}
      >
        Delete reviewed files
      </button>
    </section>
  );
}

function formatBytes(value: number): string {
  return `${value.toLocaleString('en-US')} bytes`;
}

const styles: Record<string, React.CSSProperties> = {
  shell: {
    display: 'grid',
    gap: 14,
    color: '#17202a',
    fontFamily: 'Inter, Segoe UI, Arial, sans-serif',
    border: '1px solid #d9e1e8',
    borderRadius: 8,
    padding: 16,
    background: '#ffffff',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 12,
  },
  title: {
    margin: 0,
    fontSize: 18,
    letterSpacing: 0,
  },
  meta: {
    margin: '6px 0 0',
    color: '#52606d',
    fontSize: 13,
    fontWeight: 700,
  },
  reviewBadge: {
    borderRadius: 999,
    padding: '5px 10px',
    background: '#fff4d6',
    color: '#7a4b00',
    fontWeight: 700,
    fontSize: 13,
  },
  emptyBadge: {
    borderRadius: 999,
    padding: '5px 10px',
    background: '#e8f5ec',
    color: '#1c6638',
    fontWeight: 700,
    fontSize: 13,
  },
  list: {
    display: 'grid',
    gap: 8,
    margin: 0,
    padding: 0,
    listStyle: 'none',
  },
  item: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 12,
    padding: 10,
    border: '1px solid #d7dde8',
    borderRadius: 8,
    background: '#f8fafc',
  },
  type: {
    display: 'block',
    marginTop: 4,
    color: '#52606d',
    fontSize: 12,
    fontWeight: 700,
  },
  bytes: {
    whiteSpace: 'nowrap',
    color: '#17324d',
    fontWeight: 900,
  },
  empty: {
    margin: 0,
    color: '#52606d',
    fontWeight: 700,
  },
  dangerButton: {
    justifySelf: 'start',
    border: '1px solid #a93412',
    borderRadius: 6,
    padding: '8px 12px',
    background: '#a93412',
    color: '#ffffff',
    fontWeight: 700,
  },
};
