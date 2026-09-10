import { useCallback, useEffect, useState } from 'react';

import { apiJson } from '../../shared/api';
import { CleanupPreview, type CleanupPlan } from '../storage/CleanupPreview';

type DiskDecision = {
  allowed: boolean;
  level: string;
  usedBytes: number;
  freeBytes: number;
  estimatedBytes: number;
  reasons: string[];
};

type BackupSummary = {
  id: string;
  sha256: string;
  byteSize: number;
  verified: boolean;
  verificationError: string | null;
};

type RetentionPlan = {
  requestedCount: number;
  currentCount: number;
  kept: string[];
  deletable: string[];
  applied: boolean;
  requiresConfirmation: boolean;
  pruneOnCreate: boolean;
};

/**
 * U02/U10: the Storage settings screen.
 *
 * It only calls endpoints that are safe by construction:
 * - the retention control returns a **plan** (`applied: false`); nothing is
 *   deleted by changing the number, and the screen says which backups would go;
 * - the cleanup flow shows the reviewed plan first and only deletes when the user
 *   presses the button (with the plan id + snapshot hash the backend requires);
 * - a copy restore demands the destination typed out exactly (the backend rejects
 *   anything else), and the source data root is never targeted.
 */
export function StorageSettings() {
  const [disk, setDisk] = useState<DiskDecision | null>(null);
  const [backups, setBackups] = useState<BackupSummary[]>([]);
  const [retentionCount, setRetentionCount] = useState(7);
  const [retention, setRetention] = useState<RetentionPlan | null>(null);
  const [cleanup, setCleanup] = useState<CleanupPlan | null>(null);
  const [restoreTarget, setRestoreTarget] = useState('');
  const [restoreConfirm, setRestoreConfirm] = useState('');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setError('');
    try {
      const [diskPayload, backupsPayload] = await Promise.all([
        apiJson<DiskDecision>('/api/storage/disk'),
        apiJson<{ backups: BackupSummary[] }>('/api/storage/backups'),
      ]);
      setDisk(diskPayload);
      setBackups(backupsPayload.backups);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : 'STORAGE_LOAD_FAILED');
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function previewRetention(count: number) {
    setMessage('');
    setError('');
    setRetentionCount(count);
    try {
      const plan = await apiJson<RetentionPlan>(`/api/storage/retention?count=${count}`);
      setRetention(plan);
      setMessage('Đã xem trước thay đổi retention — chưa xoá bản sao nào.');
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : 'RETENTION_PREVIEW_FAILED');
    }
  }

  async function previewCleanup() {
    setMessage('');
    setError('');
    try {
      const plan = await apiJson<CleanupPlan>('/api/storage/cleanup/preview');
      setCleanup(plan);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : 'CLEANUP_PREVIEW_FAILED');
    }
  }

  async function executeCleanup(planId: string, snapshotHash: string) {
    setMessage('');
    setError('');
    try {
      await apiJson('/api/storage/cleanup/execute', {
        method: 'POST',
        body: { planId, snapshotHash },
      });
      setCleanup(null);
      setMessage('Đã xoá đúng các tệp trong kế hoạch đã xem.');
      await load();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : 'CLEANUP_EXECUTE_FAILED');
    }
  }

  async function restoreCopy(backupId: string) {
    setMessage('');
    setError('');
    try {
      await apiJson('/api/storage/backups/' + backupId + '/restore-copy', {
        method: 'POST',
        body: { targetDataRoot: restoreTarget, confirmTarget: restoreConfirm },
      });
      setMessage('Đã phục hồi bản sao vào thư mục mới; dữ liệu gốc không bị thay đổi.');
      setRestoreConfirm('');
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : 'RESTORE_COPY_FAILED');
    }
  }

  const targetMatches = restoreTarget.trim().length > 0 && restoreTarget.trim() === restoreConfirm.trim();

  return (
    <section aria-label="Storage" style={styles.shell}>
      <h2 style={styles.title}>Storage</h2>

      {disk ? (
        <p style={styles.meta} aria-label="Dung lượng">
          Dung lượng: dùng {formatBytes(disk.usedBytes)} / còn {formatBytes(disk.freeBytes)} · mức {disk.level}
          {disk.allowed ? '' : ' · không đủ chỗ cho tác vụ tiếp theo'}
        </p>
      ) : null}

      <section aria-label="Bản sao lưu" style={styles.block}>
        <header style={styles.blockHeader}>
          <strong>Bản sao lưu ({backups.length})</strong>
          <button type="button" onClick={() => void load()} style={styles.secondary}>
            Làm mới
          </button>
        </header>
        {backups.length === 0 ? <p style={styles.meta}>Chưa có bản sao lưu nào.</p> : null}
        {backups.map((backup) => (
          <article key={backup.id} style={styles.row}>
            <div>
              <code>{backup.id}</code>
              <span style={backup.verified ? styles.ok : styles.bad}>
                {backup.verified ? 'checksum OK' : 'checksum lỗi'}
              </span>
              {backup.verificationError ? <p style={styles.meta}>{backup.verificationError}</p> : null}
            </div>
            <span style={styles.meta}>{formatBytes(backup.byteSize)}</span>
          </article>
        ))}
      </section>

      <section aria-label="Retention" style={styles.block}>
        <strong>Retention</strong>
        <p style={styles.meta}>
          Đổi số lượng chỉ tạo kế hoạch — không tự xoá. Bản sao mới vẫn prune theo số này khi tạo.
        </p>
        <div style={styles.actions}>
          <label style={styles.label}>
            Giữ lại
            <input
              aria-label="Số bản sao giữ lại"
              type="number"
              min={1}
              value={retentionCount}
              onChange={(event) => setRetentionCount(Number(event.target.value))}
              style={styles.input}
            />
          </label>
          <button type="button" onClick={() => void previewRetention(retentionCount)} style={styles.primary}>
            Xem trước retention
          </button>
        </div>
        {retention ? (
          <div aria-label="Kế hoạch retention" style={styles.plan}>
            <p style={styles.meta}>
              Giữ {retention.kept.length}/{retention.currentCount} · sẽ xoá {retention.deletable.length}
              {retention.applied ? '' : ' · chưa áp dụng'}
            </p>
            {retention.deletable.length > 0 ? (
              <ul style={styles.list}>
                {retention.deletable.map((backupId) => (
                  <li key={backupId}>
                    <code>{backupId}</code>
                  </li>
                ))}
              </ul>
            ) : (
              <p style={styles.meta}>Không có bản sao nào vượt ngưỡng.</p>
            )}
          </div>
        ) : null}
      </section>

      <section aria-label="Dọn dẹp" style={styles.block}>
        <strong>Dọn dẹp tệp không còn dùng</strong>
        <p style={styles.meta}>Luôn xem trước danh sách chính xác trước khi xoá.</p>
        <button type="button" onClick={() => void previewCleanup()} style={styles.secondary}>
          Xem trước dọn dẹp
        </button>
        {cleanup ? <CleanupPreview plan={cleanup} onExecute={(planId, hash) => void executeCleanup(planId, hash)} /> : null}
      </section>

      <section aria-label="Phục hồi vào bản sao" style={styles.block}>
        <strong>Phục hồi vào bản sao</strong>
        <p style={styles.meta}>
          Nhập thư mục mới rồi gõ lại y hệt để xác nhận. Dữ liệu gốc không bị ghi đè.
        </p>
        <label style={styles.label}>
          Thư mục đích
          <input
            aria-label="Thư mục đích"
            value={restoreTarget}
            onChange={(event) => setRestoreTarget(event.target.value)}
            style={styles.input}
          />
        </label>
        <label style={styles.label}>
          Gõ lại thư mục đích
          <input
            aria-label="Xác nhận thư mục đích"
            value={restoreConfirm}
            onChange={(event) => setRestoreConfirm(event.target.value)}
            style={styles.input}
          />
        </label>
        <div style={styles.actions}>
          {backups
            .filter((backup) => backup.verified)
            .map((backup) => (
              <button
                key={backup.id}
                type="button"
                disabled={!targetMatches}
                onClick={() => void restoreCopy(backup.id)}
                style={{ ...styles.primary, opacity: targetMatches ? 1 : 0.6 }}
              >
                Phục hồi {backup.id} vào bản sao
              </button>
            ))}
        </div>
        {!targetMatches ? (
          <p style={styles.meta} role="status">
            Cần gõ lại đúng thư mục đích để bật nút phục hồi.
          </p>
        ) : null}
      </section>

      {message ? <p role="status" style={styles.ok}>{message}</p> : null}
      {error ? <p role="alert" style={styles.bad}>{error}</p> : null}
    </section>
  );
}

function formatBytes(value: number): string {
  if (value < 1024) {
    return `${value} B`;
  }
  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(1)} KB`;
  }
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

const styles: Record<string, React.CSSProperties> = {
  shell: { display: 'grid', gap: 14, maxWidth: 720 },
  title: { margin: 0, fontSize: 15 },
  meta: { margin: 0, fontSize: 13, color: '#475467' },
  block: { display: 'grid', gap: 8, padding: 12, border: '1px solid #e2e8f0', borderRadius: 8, background: '#f8fafc' },
  blockHeader: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 },
  row: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, padding: '6px 0' },
  ok: { color: '#166534', fontWeight: 700, marginLeft: 8, fontSize: 12 },
  bad: { color: '#9a3412', fontWeight: 700, marginLeft: 8, fontSize: 12 },
  label: { display: 'grid', gap: 4, fontSize: 13, fontWeight: 700 },
  input: { padding: '8px 10px', border: '1px solid #c8d1dc', borderRadius: 6, font: 'inherit' },
  actions: { display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end' },
  primary: { padding: '8px 12px', border: 0, borderRadius: 6, background: '#155eef', color: '#ffffff', fontWeight: 700 },
  secondary: { padding: '8px 12px', border: '1px solid #c8d1dc', borderRadius: 6, background: '#ffffff', fontWeight: 700 },
  plan: { display: 'grid', gap: 6 },
  list: { margin: 0, paddingLeft: 18, fontSize: 13 },
};
