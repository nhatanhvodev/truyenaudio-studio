import { useChapterDraft, type DraftContent, type DraftConflict } from './useChapterDraft';

import styles from './DraftControls.module.css';

type Props = {
  chapterId: string;
  baseRevisionId: string | null;
  content: DraftContent;
  onRestore: (content: DraftContent) => void;
};

/**
 * U04 round 2: draft toolbar for the translation editor.
 *
 * Restores the server draft on mount, saves with the draft revision as the
 * compare-and-swap expectation, and shows the conflict diff when the server
 * moved on — the local text is never replaced without an explicit choice.
 */
export default function DraftControls({ chapterId, baseRevisionId, content, onRestore }: Props) {
  const draft = useChapterDraft({ chapterId, baseRevisionId, onRestore });

  if (!baseRevisionId) {
    return (
      <p className={styles.disabled} role="status">
        Chưa có bản nguồn đang hoạt động nên chưa lưu được nháp.
      </p>
    );
  }

  return (
    <section className={styles.shell} aria-label="Bản nháp">
      <div className={styles.row}>
        <span className={styles.revision}>
          Nháp: {draft.revision === null ? 'chưa có' : `bản ${draft.revision}`}
        </span>
        <button
          type="button"
          onClick={() => void draft.save(content)}
          disabled={draft.saving}
          className={styles.primaryButton}
        >
          Lưu nháp
        </button>
        <button type="button" onClick={() => void draft.reload()} className={styles.secondaryButton}>
          Tải lại nháp
        </button>
      </div>

      {draft.message ? (
        <p role="status" className={styles.success}>
          {draft.message}
        </p>
      ) : null}
      {draft.error && !draft.conflict ? (
        <p role="alert" className={styles.error}>
          {draft.error}
        </p>
      ) : null}

      {draft.conflict ? (
        <div className={styles.conflict} role="alert" aria-label="Xung đột bản nháp">
          <p className={styles.conflictTitle}>
            Bản nháp trên máy chủ đã thay đổi (bản {formatRevision(draft.conflict)}). Bản đang sửa của bạn
            vẫn được giữ nguyên.
          </p>
          <ul className={styles.diff}>
            {draft.conflict.changedSegmentIds.map((segmentId) => (
              <li key={segmentId}>
                Đoạn {segmentId}: “{truncate(draft.conflict?.serverContent[segmentId])}” → “
                {truncate(draft.conflict?.localContent[segmentId])}”
              </li>
            ))}
          </ul>
          <div className={styles.row}>
            <button type="button" onClick={() => void draft.overwrite()} className={styles.primaryButton}>
              Ghi đè bằng bản của tôi
            </button>
            <button type="button" onClick={() => draft.useServerContent()} className={styles.secondaryButton}>
              Dùng bản trên máy chủ
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function formatRevision(conflict: DraftConflict): string {
  return conflict.serverRevision === null ? 'chưa có' : String(conflict.serverRevision);
}

function truncate(value: string | undefined): string {
  const text = value ?? '';
  return text.length > 40 ? `${text.slice(0, 40)}…` : text;
}
