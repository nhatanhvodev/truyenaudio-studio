import { Button } from '../../shared/ui';

import styles from './CleanupPreview.module.css';

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
    <section aria-label="Cleanup preview" className={styles.shell}>
      <header className={styles.header}>
        <div>
          <h2 className={styles.title}>Storage cleanup</h2>
          <p className={styles.meta}>{formatBytes(plan.totalBytes)} reviewed for deletion</p>
        </div>
        <span className={empty ? styles.emptyBadge : styles.reviewBadge}>{empty ? 'No candidates' : 'Preview only'}</span>
      </header>

      {empty ? (
        <p className={styles.empty}>No reviewed cleanup candidates.</p>
      ) : (
        <ul className={styles.list}>
          {plan.candidates.map((candidate) => (
            <li key={`${candidate.candidateType}:${candidate.relativePath}`} className={styles.item}>
              <div>
                <strong>{candidate.relativePath}</strong>
                <span className={styles.type}>{candidate.candidateType}</span>
              </div>
              <span className={styles.bytes}>{formatBytes(candidate.byteSize)}</span>
            </li>
          ))}
        </ul>
      )}

      <div className={styles.actions}>
        <Button
          variant="danger"
          disabled={empty}
          onClick={() => onExecute(plan.planId, plan.snapshotHash)}
        >
          Delete reviewed files
        </Button>
      </div>
    </section>
  );
}

function formatBytes(value: number): string {
  return `${value.toLocaleString('en-US')} bytes`;
}
