type Replacement = {
  sourceSegmentId: string;
  sourceText: string;
  currentTargetText: string;
  targetText: string;
};

type Proposal = {
  id: string;
  baseRunId: string;
  estimatedCostVnd: number;
  /**
   * Which engine produced the proposal. "OFFLINE_DETERMINISTIC" is the local
   * converter (no network, no model), and the diff says so before an operator
   * can accept it — accepting writes a new translation run.
   */
  generator?: string;
  hash: string;
  replacements: Replacement[];
};

const OFFLINE_GENERATOR = 'OFFLINE_DETERMINISTIC';
const UNKNOWN_GENERATOR_NOTE =
  'Chưa xác định được nguồn sinh đề xuất này. Kiểm tra từng dòng trước khi chấp nhận — chấp nhận sẽ ghi một run dịch mới.';

type Props = {
  proposal: Proposal;
  onAccept: (proposalId: string, expectedHash: string) => void;
  onReject: (proposalId: string) => void;
};

export default function RepairDiff({ proposal, onAccept, onReject }: Props) {
  return (
    <section aria-label="Repair diff" style={styles.shell}>
      <header style={styles.header}>
        <div>
          <h2 style={styles.title}>Selective Repair</h2>
          <p style={styles.meta}>Run {proposal.baseRunId}</p>
        </div>
        <p style={styles.cost}>{formatVnd(proposal.estimatedCostVnd)}</p>
      </header>

      {proposal.generator === OFFLINE_GENERATOR ? (
        <p role="status" data-testid="repair-generator-note" style={styles.generatorNote}>
          Đề xuất do bộ chuyển đổi <strong>offline tất định</strong> tạo, không gọi model cloud và không phát sinh chi
          phí (đường cloud thuộc J01). Kiểm tra từng dòng trước khi chấp nhận.
        </p>
      ) : (
        // Fail-closed: a payload that omits or renames the marker gets the caution
        // too. Only a generator explicitly known to be model-backed may be silent,
        // and none exists until J01 wires the cloud repair.
        <p role="status" data-testid="repair-generator-note" style={styles.generatorNote}>
          {UNKNOWN_GENERATOR_NOTE}
        </p>
      )}

      <div style={styles.list}>
        {proposal.replacements.map((replacement) => (
          <article key={replacement.sourceSegmentId} style={styles.item}>
            <p style={styles.source}>{replacement.sourceText}</p>
            <Diff current={replacement.currentTargetText} next={replacement.targetText} />
          </article>
        ))}
      </div>

      <footer style={styles.actions}>
        <button type="button" style={styles.secondaryButton} onClick={() => onReject(proposal.id)}>
          Reject
        </button>
        <button type="button" style={styles.primaryButton} onClick={() => onAccept(proposal.id, proposal.hash)}>
          Accept repair
        </button>
      </footer>
    </section>
  );
}

function Diff({ current, next }: { current: string; next: string }) {
  if (current === next) {
    return <p style={styles.unchanged}>{current}</p>;
  }

  const oldWords = current.split(/\s+/).filter(Boolean);
  const newWords = next.split(/\s+/).filter(Boolean);
  return (
    <div style={styles.diff} aria-label="Word diff">
      <p style={styles.line} aria-label="Current target">
        {oldWords.map((word, index) => (
          <del key={`${word}-${index}`} style={styles.removed}>
            {word}
            {index < oldWords.length - 1 ? ' ' : ''}
          </del>
        ))}
      </p>
      <p style={styles.line} aria-label="Proposed target">
        {newWords.map((word, index) => (
          <ins key={`${word}-${index}`} style={styles.added}>
            {word}
            {index < newWords.length - 1 ? ' ' : ''}
          </ins>
        ))}
      </p>
    </div>
  );
}

function formatVnd(value: number) {
  return new Intl.NumberFormat('vi-VN', { style: 'currency', currency: 'VND', maximumFractionDigits: 0 }).format(value);
}

const styles: Record<string, React.CSSProperties> = {
  shell: {
    display: 'grid',
    gap: 16,
    padding: 20,
    color: '#172033',
    background: '#f6f7f9',
    fontFamily: 'Inter, Segoe UI, Arial, sans-serif',
  },
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    gap: 16,
  },
  title: {
    margin: 0,
    fontSize: 22,
    letterSpacing: 0,
  },
  meta: {
    margin: '4px 0 0',
    color: '#5f6b7a',
  },
  cost: {
    margin: 0,
    fontWeight: 800,
  },
  generatorNote: {
    margin: 0,
    padding: '8px 10px',
    border: '1px solid #f0c36d',
    borderRadius: 6,
    background: '#fffaeb',
    color: '#7a4b00',
    lineHeight: 1.5,
  },
  list: {
    display: 'grid',
    gap: 10,
  },
  item: {
    display: 'grid',
    gap: 10,
    padding: 12,
    border: '1px solid #d8dee8',
    borderRadius: 8,
    background: '#ffffff',
  },
  source: {
    margin: 0,
    color: '#39465a',
    lineHeight: 1.5,
  },
  diff: {
    display: 'grid',
    gap: 6,
  },
  line: {
    margin: 0,
    lineHeight: 1.6,
  },
  removed: {
    color: '#9f1239',
    background: '#ffe4e6',
    textDecoration: 'line-through',
  },
  added: {
    color: '#166534',
    background: '#dcfce7',
    textDecoration: 'none',
  },
  unchanged: {
    margin: 0,
    color: '#334155',
  },
  actions: {
    display: 'flex',
    justifyContent: 'flex-end',
    gap: 10,
  },
  secondaryButton: {
    border: '1px solid #c9d2df',
    borderRadius: 6,
    background: '#ffffff',
    color: '#172033',
    padding: '9px 13px',
    fontWeight: 800,
  },
  primaryButton: {
    border: 0,
    borderRadius: 6,
    background: '#155eef',
    color: '#ffffff',
    padding: '9px 13px',
    fontWeight: 800,
  },
};
