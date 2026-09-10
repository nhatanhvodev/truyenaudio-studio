import { useJobDraftStream, type EventSourceLike } from './useJobDraftStream';

type Props = {
  jobId: string;
  eventSourceFactory?: (url: string) => EventSourceLike | null;
};

/**
 * U07 round 1: read-only view of a running translation draft (J04 feed).
 *
 * The panel shows streamed text with its offset and status, warns on truncation,
 * offers an explicit resync, and states clearly that a draft is never
 * approvable — approving happens in the translation review screen.
 */
export default function JobDraftPanel({ jobId, eventSourceFactory }: Props) {
  const draft = useJobDraftStream({ jobId, eventSourceFactory });

  return (
    <section style={styles.shell} aria-label="Bản nháp job">
      <header style={styles.header}>
        <h2 style={styles.title}>Nháp đang dịch</h2>
        <span style={styles.badge}>{draft.status}</span>
      </header>

      <p style={styles.meta} aria-label="Trạng thái nháp">
        Offset {draft.offset}
        {draft.draftRevision === null ? '' : ` · nháp bản ${draft.draftRevision}`}
        {draft.connected ? ' · đang nhận trực tiếp' : ' · chưa kết nối'}
      </p>

      {draft.loading ? <p role="status">Đang tải nháp…</p> : null}
      {draft.error ? <p role="alert">{draft.error}</p> : null}
      {draft.truncated ? (
        <p role="status" style={styles.warning}>
          Luồng bị cắt bớt để giới hạn bộ nhớ — bấm “Tải lại nháp” để lấy bản đầy đủ.
        </p>
      ) : null}

      <textarea
        aria-label="Nội dung nháp"
        value={draft.text}
        readOnly
        rows={10}
        style={styles.textarea}
        placeholder="Chưa có văn bản nháp nào."
      />

      <div style={styles.row}>
        <button type="button" onClick={() => void draft.resync()} style={styles.secondary}>
          Tải lại nháp
        </button>
        <span style={styles.note}>
          {draft.approvable
            ? 'Bản nháp không dùng để duyệt — hãy sang màn Dịch &amp; hiệu đính.'
            : 'Nháp chỉ để xem; mọi phê duyệt thực hiện ở màn Dịch & hiệu đính.'}
        </span>
      </div>
    </section>
  );
}

const styles: Record<string, React.CSSProperties> = {
  shell: { display: 'grid', gap: 10, padding: 16, border: '1px solid #d9e1ea', borderRadius: 8, background: '#ffffff' },
  header: { display: 'flex', alignItems: 'center', gap: 10 },
  title: { margin: 0, fontSize: 18 },
  badge: { padding: '2px 8px', borderRadius: 999, background: '#eef2ff', color: '#3730a3', fontSize: 12, fontWeight: 700 },
  meta: { margin: 0, fontSize: 13, color: '#667085' },
  warning: { margin: 0, color: '#92400e', fontWeight: 700 },
  textarea: {
    width: '100%',
    boxSizing: 'border-box',
    padding: 12,
    border: '1px solid #c8d1dc',
    borderRadius: 6,
    font: 'inherit',
    lineHeight: 1.6,
    resize: 'vertical',
    background: '#fbfcfe',
  },
  row: { display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 10 },
  secondary: { padding: '8px 12px', border: '1px solid #c8d1dc', borderRadius: 6, background: '#ffffff', fontWeight: 700 },
  note: { fontSize: 13, color: '#475467', fontWeight: 600 },
};
