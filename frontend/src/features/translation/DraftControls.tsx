import { useChapterDraft, type DraftContent, type DraftConflict } from './useChapterDraft';

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
      <p style={styles.disabled} role="status">
        Chưa có bản nguồn đang hoạt động nên chưa lưu được nháp.
      </p>
    );
  }

  return (
    <section style={styles.shell} aria-label="Bản nháp">
      <div style={styles.row}>
        <span style={styles.revision}>
          Nháp: {draft.revision === null ? 'chưa có' : `bản ${draft.revision}`}
        </span>
        <button
          type="button"
          onClick={() => void draft.save(content)}
          disabled={draft.saving}
          style={styles.primary}
        >
          Lưu nháp
        </button>
        <button type="button" onClick={() => void draft.reload()} style={styles.secondary}>
          Tải lại nháp
        </button>
      </div>

      {draft.message ? (
        <p role="status" style={styles.success}>
          {draft.message}
        </p>
      ) : null}
      {draft.error && !draft.conflict ? (
        <p role="alert" style={styles.error}>
          {draft.error}
        </p>
      ) : null}

      {draft.conflict ? (
        <div style={styles.conflict} role="alert" aria-label="Xung đột bản nháp">
          <p style={styles.conflictTitle}>
            Bản nháp trên máy chủ đã thay đổi (bản {formatRevision(draft.conflict)}). Bản đang sửa của bạn
            vẫn được giữ nguyên.
          </p>
          <ul style={styles.diff}>
            {draft.conflict.changedSegmentIds.map((segmentId) => (
              <li key={segmentId}>
                Đoạn {segmentId}: “{truncate(draft.conflict?.serverContent[segmentId])}” → “
                {truncate(draft.conflict?.localContent[segmentId])}”
              </li>
            ))}
          </ul>
          <div style={styles.row}>
            <button type="button" onClick={() => void draft.overwrite()} style={styles.primary}>
              Ghi đè bằng bản của tôi
            </button>
            <button type="button" onClick={() => draft.useServerContent()} style={styles.secondary}>
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

const styles: Record<string, React.CSSProperties> = {
  shell: {
    display: 'grid',
    gap: 8,
    padding: 12,
    border: '1px solid #d9e1ea',
    borderRadius: 8,
    background: '#ffffff',
  },
  row: {
    display: 'flex',
    flexWrap: 'wrap',
    alignItems: 'center',
    gap: 8,
  },
  revision: {
    fontSize: 13,
    fontWeight: 700,
    color: '#475467',
  },
  primary: {
    padding: '8px 12px',
    border: 0,
    borderRadius: 6,
    background: '#155eef',
    color: '#ffffff',
    fontWeight: 700,
  },
  secondary: {
    padding: '8px 12px',
    border: '1px solid #c8d1dc',
    borderRadius: 6,
    background: '#ffffff',
    color: '#18212f',
    fontWeight: 700,
  },
  success: {
    margin: 0,
    color: '#166534',
    fontWeight: 700,
  },
  error: {
    margin: 0,
    color: '#9a3412',
    fontWeight: 700,
  },
  conflict: {
    display: 'grid',
    gap: 8,
    padding: 10,
    border: '1px solid #fbbf24',
    borderRadius: 6,
    background: '#fffbeb',
  },
  conflictTitle: {
    margin: 0,
    fontWeight: 700,
    color: '#7a4b00',
  },
  diff: {
    margin: 0,
    paddingLeft: 18,
    color: '#7a4b00',
    fontSize: 13,
    lineHeight: 1.5,
  },
  disabled: {
    margin: 0,
    color: '#586274',
  },
};
