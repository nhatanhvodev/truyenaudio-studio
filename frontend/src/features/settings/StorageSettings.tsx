import { useCallback, useEffect, useState } from 'react';

import { apiJson } from '../../shared/api';
import { Button } from '../../shared/ui';
import { CleanupPreview, type CleanupPlan } from '../storage/CleanupPreview';

import styles from './StorageSettings.module.css';

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
    <section aria-label="Storage" className={styles.shell}>
      <h2 className={styles.title}>Storage</h2>

      {disk ? (
        <p className={styles.meta} aria-label="Dung lượng">
          Dung lượng: dùng {formatBytes(disk.usedBytes)} / còn {formatBytes(disk.freeBytes)} · mức {disk.level}
          {disk.allowed ? '' : ' · không đủ chỗ cho tác vụ tiếp theo'}
        </p>
      ) : null}

      <section aria-label="Bản sao lưu" className={styles.block}>
        <header className={styles.blockHeader}>
          <strong>Bản sao lưu ({backups.length})</strong>
          <Button variant="secondary" onClick={() => void load()}>
            Làm mới
          </Button>
        </header>
        {backups.length === 0 ? <p className={styles.meta}>Chưa có bản sao lưu nào.</p> : null}
        {backups.map((backup) => (
          <article key={backup.id} className={styles.row}>
            <div>
              <code>{backup.id}</code>
              <span className={backup.verified ? styles.ok : styles.bad}>
                {backup.verified ? 'checksum OK' : 'checksum lỗi'}
              </span>
              {backup.verificationError ? <p className={styles.meta}>{backup.verificationError}</p> : null}
            </div>
            <span className={styles.meta}>{formatBytes(backup.byteSize)}</span>
          </article>
        ))}
      </section>

      <section aria-label="Retention" className={styles.block}>
        <strong>Retention</strong>
        <p className={styles.meta}>
          Đổi số lượng chỉ tạo kế hoạch — không tự xoá. Bản sao mới vẫn prune theo số này khi tạo.
        </p>
        <div className={styles.actions}>
          <label className={styles.field}>
            Giữ lại
            <input
              aria-label="Số bản sao giữ lại"
              type="number"
              min={1}
              value={retentionCount}
              onChange={(event) => setRetentionCount(Number(event.target.value))}
              className={styles.input}
            />
          </label>
          <Button variant="primary" onClick={() => void previewRetention(retentionCount)}>
            Xem trước retention
          </Button>
        </div>
        {retention ? (
          <div aria-label="Kế hoạch retention" className={styles.plan}>
            <p className={styles.meta}>
              Giữ {retention.kept.length}/{retention.currentCount} · sẽ xoá {retention.deletable.length}
              {retention.applied ? '' : ' · chưa áp dụng'}
            </p>
            {retention.deletable.length > 0 ? (
              <ul className={styles.list}>
                {retention.deletable.map((backupId) => (
                  <li key={backupId}>
                    <code>{backupId}</code>
                  </li>
                ))}
              </ul>
            ) : (
              <p className={styles.meta}>Không có bản sao nào vượt ngưỡng.</p>
            )}
          </div>
        ) : null}
      </section>

      <section aria-label="Dọn dẹp" className={styles.block}>
        <strong>Dọn dẹp tệp không còn dùng</strong>
        <p className={styles.meta}>Luôn xem trước danh sách chính xác trước khi xoá.</p>
        <Button variant="secondary" onClick={() => void previewCleanup()}>
          Xem trước dọn dẹp
        </Button>
        {cleanup ? <CleanupPreview plan={cleanup} onExecute={(planId, hash) => void executeCleanup(planId, hash)} /> : null}
      </section>

      <section aria-label="Phục hồi vào bản sao" className={styles.block}>
        <strong>Phục hồi vào bản sao</strong>
        <p className={styles.meta}>
          Nhập thư mục mới rồi gõ lại y hệt để xác nhận. Dữ liệu gốc không bị ghi đè.
        </p>
        <label className={styles.field}>
          Thư mục đích
          <input
            aria-label="Thư mục đích"
            value={restoreTarget}
            onChange={(event) => setRestoreTarget(event.target.value)}
            className={styles.input}
          />
        </label>
        <label className={styles.field}>
          Gõ lại thư mục đích
          <input
            aria-label="Xác nhận thư mục đích"
            value={restoreConfirm}
            onChange={(event) => setRestoreConfirm(event.target.value)}
            className={styles.input}
          />
        </label>
        <div className={styles.actions}>
          {backups
            .filter((backup) => backup.verified)
            .map((backup) => (
              <Button
                key={backup.id}
                variant="primary"
                disabled={!targetMatches}
                onClick={() => void restoreCopy(backup.id)}
              >
                Phục hồi {backup.id} vào bản sao
              </Button>
            ))}
        </div>
        {!targetMatches ? (
          <p className={styles.meta} role="status">
            Cần gõ lại đúng thư mục đích để bật nút phục hồi.
          </p>
        ) : null}
      </section>

      {message ? <p role="status" className={styles.okText}>{message}</p> : null}
      {error ? <p role="alert" className={styles.badText}>{error}</p> : null}
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
