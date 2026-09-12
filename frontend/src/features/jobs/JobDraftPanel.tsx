import { useJobDraftStream, type EventSourceLike } from './useJobDraftStream';

import styles from './JobDraftPanel.module.css';

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
    <section className={styles.shell} aria-label="Bản nháp job">
      <header className={styles.header}>
        <h2 className={styles.title}>Nháp đang dịch</h2>
        <span className={styles.badge}>{draft.status}</span>
      </header>

      <p className={styles.meta} aria-label="Trạng thái nháp">
        Offset {draft.offset}
        {draft.draftRevision === null ? '' : ` · nháp bản ${draft.draftRevision}`}
        {draft.connected ? ' · đang nhận trực tiếp' : ' · chưa kết nối'}
      </p>

      {draft.loading ? <p role="status">Đang tải nháp…</p> : null}
      {draft.error ? <p role="alert">{draft.error}</p> : null}
      {draft.truncated ? (
        <p role="status" className={styles.warning}>
          Luồng bị cắt bớt để giới hạn bộ nhớ — bấm “Tải lại nháp” để lấy bản đầy đủ.
        </p>
      ) : null}

      <textarea
        aria-label="Nội dung nháp"
        value={draft.text}
        readOnly
        rows={10}
        className={styles.textarea}
        placeholder="Chưa có văn bản nháp nào."
      />

      <div className={styles.row}>
        <button type="button" onClick={() => void draft.resync()} className={styles.secondaryButton}>
          Tải lại nháp
        </button>
        <span className={styles.note}>
          {draft.approvable
            ? 'Bản nháp không dùng để duyệt — hãy sang màn Dịch &amp; hiệu đính.'
            : 'Nháp chỉ để xem; mọi phê duyệt thực hiện ở màn Dịch & hiệu đính.'}
        </span>
      </div>
    </section>
  );
}
