import styles from './RepairDiff.module.css';

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
    <section aria-label="Repair diff" className={styles.shell}>
      <header className={styles.header}>
        <div>
          <h2 className={styles.title}>Selective Repair</h2>
          <p className={styles.meta}>Run {proposal.baseRunId}</p>
        </div>
        <p className={styles.cost}>{formatVnd(proposal.estimatedCostVnd)}</p>
      </header>

      {proposal.generator === OFFLINE_GENERATOR ? (
        <p role="status" data-testid="repair-generator-note" className={styles.generatorNote}>
          Đề xuất do bộ chuyển đổi <strong>offline tất định</strong> tạo, không gọi model cloud và không phát sinh chi
          phí (đường cloud thuộc J01). Kiểm tra từng dòng trước khi chấp nhận.
        </p>
      ) : (
        // Fail-closed: a payload that omits or renames the marker gets the caution
        // too. Only a generator explicitly known to be model-backed may be silent,
        // and none exists until J01 wires the cloud repair.
        <p role="status" data-testid="repair-generator-note" className={styles.generatorNote}>
          {UNKNOWN_GENERATOR_NOTE}
        </p>
      )}

      <div className={styles.list}>
        {proposal.replacements.map((replacement) => (
          <article key={replacement.sourceSegmentId} className={styles.item}>
            <p className={styles.source}>{replacement.sourceText}</p>
            <Diff current={replacement.currentTargetText} next={replacement.targetText} />
          </article>
        ))}
      </div>

      <footer className={styles.actions}>
        <button type="button" className={styles.secondaryButton} onClick={() => onReject(proposal.id)}>
          Reject
        </button>
        <button type="button" className={styles.primaryButton} onClick={() => onAccept(proposal.id, proposal.hash)}>
          Accept repair
        </button>
      </footer>
    </section>
  );
}

function Diff({ current, next }: { current: string; next: string }) {
  if (current === next) {
    return <p className={styles.unchanged}>{current}</p>;
  }

  const oldWords = current.split(/\s+/).filter(Boolean);
  const newWords = next.split(/\s+/).filter(Boolean);
  return (
    <div className={styles.diff} aria-label="Word diff">
      <p className={styles.line} aria-label="Current target">
        {oldWords.map((word, index) => (
          <del key={`${word}-${index}`} className={styles.removed}>
            {word}
            {index < oldWords.length - 1 ? ' ' : ''}
          </del>
        ))}
      </p>
      <p className={styles.line} aria-label="Proposed target">
        {newWords.map((word, index) => (
          <ins key={`${word}-${index}`} className={styles.added}>
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
