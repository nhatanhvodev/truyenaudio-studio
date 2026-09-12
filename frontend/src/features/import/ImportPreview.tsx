import styles from './ImportPreview.module.css';

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
    <section className={styles.section} aria-label="Import preview">
      <header className={styles.header}>
        <div className={styles.heading}>
          <h2 className={styles.sectionTitle}>Import Preview</h2>
          <p className={styles.meta}>{candidates.length} candidates ready for mapping review</p>
        </div>
        <button type="button" onClick={() => onConfirm(candidates)} disabled={candidates.length === 0} className={styles.confirmButton}>
          Confirm import mapping
        </button>
      </header>

      <div className={styles.list}>
        {candidates.map((candidate, index) => (
          <article key={`${candidate.sourcePath}-${index}`} className={styles.candidate}>
            <span className={styles.ordinal}>{candidate.ordinal ?? '-'}</span>
            <div className={styles.body}>
              <h3 className={styles.itemTitle}>{candidate.title ?? 'Untitled chapter'}</h3>
              <p className={styles.sourcePath}>{candidate.sourcePath}</p>
              <p className={styles.excerpt}>{candidate.text || 'No preview text'}</p>
            </div>
            {candidate.warnings.length > 0 ? (
              <div className={styles.warnings} aria-label={`Warnings for ${candidate.sourcePath}`}>
                {candidate.warnings.map((warning) => (
                  <span key={warning} className={styles.warning}>
                    {warning}
                  </span>
                ))}
              </div>
            ) : null}
          </article>
        ))}
      </div>
    </section>
  );
}

