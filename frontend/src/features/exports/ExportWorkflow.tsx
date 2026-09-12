import { useCallback, useEffect, useMemo, useState } from 'react';
import { apiJson } from '../../shared/api';
import { Button, Input } from '../../shared/ui';

import styles from './ExportWorkflow.module.css';

type GateDecision = {
  allowed: boolean;
  reasons: string[];
  rightsEvaluationHash: string;
};

type BundleStatus = {
  id: string;
  kind: string;
  status: string;
  manifestSha256: string;
  artifactId: string;
  directoryPath: string;
  files: string[];
  createdAt: string;
  verified: boolean | null;
  mismatches: string[];
  stale: boolean;
  staleReasons: string[];
};

type ChapterExportStatus = {
  chapterId: string;
  gate: GateDecision;
  bundles: BundleStatus[];
};

type CreatedBundle = {
  id: string;
  kind: string;
  manifestSha256: string;
  files: string[];
};

export type ExportWorkflowProps = {
  chapterId: string;
};

/** C08: the studio hands the bundle to the operator; it never publishes itself. */
export const MANUAL_UPLOAD_NOTICE =
  'Bản xuất chỉ để tải thủ công sang app chính — studio không tự upload.';

export const PRIVATE_KIND = 'PRIVATE_ARCHIVE';
export const PUBLICATION_KIND = 'PUBLICATION_BUNDLE';

const KIND_LABELS: Record<string, string> = {
  [PRIVATE_KIND]: 'Archive riêng tư',
  [PUBLICATION_KIND]: 'Bundle publication',
};

const STALE_LABELS: Record<string, string> = {
  GATE_BLOCKED: 'Quyền hiện tại đang chặn xuất bản',
  RIGHTS_CHANGED: 'Quyền đã thay đổi kể từ khi tạo bundle',
  MASTER_CHANGED: 'Bản master đã được phê duyệt lại',
  TRANSLATION_CHANGED: 'Bản dịch chuẩn đã thay đổi',
  UNKNOWN_RIGHTS_PROVENANCE: 'Không đọc được hash quyền đã lưu trong bundle',
  UNKNOWN_MASTER_PROVENANCE: 'Không đọc được master đã ghi trong bundle',
  UNKNOWN_TRANSLATION_PROVENANCE: 'Không đọc được bản dịch đã ghi trong bundle',
  UNKNOWN_METADATA_PROVENANCE: 'Không đọc được metadata tập trong bundle',
};

const VALIDATION_LABELS: Record<string, string> = {
  EPISODE_TITLE_REQUIRED: 'Tiêu đề tập không được để trống',
  EPISODE_NUMBER_POSITIVE_REQUIRED: 'Số tập phải là số nguyên từ 1 trở lên',
};

export const FILE_GROUPS: { key: string; label: string }[] = [
  { key: 'audio', label: 'Audio' },
  { key: 'transcript', label: 'Transcript / phụ đề' },
  { key: 'metadata', label: 'Metadata' },
  { key: 'provenance', label: 'Provenance' },
  { key: 'license', label: 'License / giấy phép' },
  { key: 'checksum', label: 'Checksum' },
  { key: 'other', label: 'Khác' },
];

/** Mirrors the backend rules so a bad form never reaches the API. */
export function validateEpisodeMetadata(
  title: string,
  episodeNumber: string,
): string[] {
  const errors: string[] = [];
  if (!title.trim()) {
    errors.push('EPISODE_TITLE_REQUIRED');
  }
  const parsed = Number(episodeNumber);
  if (episodeNumber.trim() === '' || !Number.isInteger(parsed) || parsed < 1) {
    errors.push('EPISODE_NUMBER_POSITIVE_REQUIRED');
  }
  return errors;
}

export function groupBundleFiles(files: string[]): Record<string, string[]> {
  const groups: Record<string, string[]> = {};
  for (const group of FILE_GROUPS) {
    groups[group.key] = [];
  }
  for (const name of files) {
    groups[fileGroup(name)].push(name);
  }
  return groups;
}

function fileGroup(name: string): string {
  const lower = name.toLowerCase();
  if (lower.endsWith('.sha256')) {
    return 'checksum';
  }
  if (lower.includes('licen') || lower.includes('private_only')) {
    return 'license';
  }
  if (lower.includes('provenance')) {
    return 'provenance';
  }
  if (/.(mp3|wav|m4a|aac|ogg|flac)$/.test(lower)) {
    return 'audio';
  }
  if (/.(srt|vtt|md|txt)$/.test(lower)) {
    return 'transcript';
  }
  if (lower.endsWith('.json')) {
    return 'metadata';
  }
  return 'other';
}

export function ExportWorkflow({ chapterId }: ExportWorkflowProps) {
  const [status, setStatus] = useState<ChapterExportStatus | null>(null);
  const [loadError, setLoadError] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [episodeTitle, setEpisodeTitle] = useState('');
  const [episodeNumber, setEpisodeNumber] = useState('1');
  const [isPremium, setIsPremium] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<string[]>([]);
  const [fallbackBundle, setFallbackBundle] = useState<CreatedBundle | null>(null);

  const refresh = useCallback(async () => {
    const payload = await apiJson<ChapterExportStatus>(
      `/api/chapters/${chapterId}/exports/status`,
    );
    setStatus(payload);
    return payload;
  }, [chapterId]);

  useEffect(() => {
    let active = true;
    apiJson<ChapterExportStatus>(`/api/chapters/${chapterId}/exports/status`)
      .then((payload) => {
        if (active) {
          setStatus(payload);
        }
      })
      .catch((reason: unknown) => {
        if (active) {
          setLoadError(reason instanceof Error ? reason.message : 'EXPORT_STATUS_FAILED');
        }
      });
    return () => {
      active = false;
    };
  }, [chapterId]);

  async function build(kind: string) {
    if (busy) {
      return;
    }
    setError('');
    setNotice('');
    if (kind === PUBLICATION_KIND) {
      if (status && !status.gate.allowed) {
        // A private archive never grants publication rights.
        setError('GATE_BLOCKED');
        return;
      }
      const errors = validateEpisodeMetadata(episodeTitle, episodeNumber);
      setFieldErrors(errors);
      if (errors.length > 0) {
        return;
      }
    } else {
      setFieldErrors([]);
    }

    setBusy(true);
    try {
      const created = await apiJson<CreatedBundle>(
        `/api/chapters/${chapterId}/exports/${kind === PRIVATE_KIND ? 'private' : 'publication'}`,
        {
          method: 'POST',
          body:
            kind === PRIVATE_KIND
              ? {}
              : {
                  episodeTitle: episodeTitle.trim(),
                  suggestedEpisodeNumber: Number(episodeNumber),
                  isPremium,
                },
        },
      );
      setFallbackBundle(created);
      setNotice(
        kind === PRIVATE_KIND
          ? 'Đã tạo archive riêng tư.'
          : 'Đã tạo bundle publication.',
      );
      try {
        await refresh();
      } catch {
        // The card still shows the manifest from the POST response.
      }
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : 'EXPORT_FAILED');
    } finally {
      setBusy(false);
    }
  }

  const bundles = useMemo<BundleStatus[]>(() => {
    const list = status?.bundles ?? [];
    if (!fallbackBundle || list.some((item) => item.id === fallbackBundle.id)) {
      return list;
    }
    return [
      {
        ...fallbackBundle,
        status: 'READY',
        artifactId: '',
        directoryPath: '',
        createdAt: '',
        verified: null,
        mismatches: [],
        stale: false,
        staleReasons: [],
      },
      ...list,
    ];
  }, [status, fallbackBundle]);

  const blocked = status ? !status.gate.allowed : false;

  return (
    <section
      aria-label="Xuất bản và metadata"
      data-testid="export-workflow"
      className={styles.workflow}
    >
      <div
        className={[styles.gate, blocked ? styles.gateBlocked : styles.gateReady]
          .filter(Boolean)
          .join(' ')}
      >
        <h2 className={styles.title}>Xuất bản</h2>
        {status ? (
          <strong className={blocked ? styles.statusBlocked : styles.statusOk}>
            {blocked ? 'Bị chặn xuất bản' : 'Sẵn sàng xuất bản'}
          </strong>
        ) : (
          <strong className={styles.statusMuted}>Đang kiểm tra quyền</strong>
        )}
      </div>

      {status ? (
        <p className={styles.hash}>
          Hash đánh giá quyền:{' '}
          <code data-testid="rights-hash" className={styles.code}>
            {status.gate.rightsEvaluationHash.slice(0, 12)}…
          </code>
        </p>
      ) : null}

      {blocked ? (
        <ul aria-label="Lý do bị chặn" className={styles.gateReasons}>
          {status?.gate.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      ) : null}

      {loadError ? (
        <p role="alert" className={styles.error}>
          Không tải được trạng thái export: {loadError}
        </p>
      ) : null}

      <fieldset className={styles.fieldset}>
        <legend className={styles.legend}>Metadata tập</legend>
        <div className={styles.fields}>
          <Input
            label="Tiêu đề tập"
            value={episodeTitle}
            onChange={(event) => setEpisodeTitle(event.target.value)}
          />
          <Input
            label="Số tập"
            type="number"
            min={1}
            value={episodeNumber}
            onChange={(event) => setEpisodeNumber(event.target.value)}
          />
        </div>
        <label className={styles.checkbox}>
          <input
            type="checkbox"
            checked={isPremium}
            onChange={(event) => setIsPremium(event.target.checked)}
          />
          Tập premium
        </label>
        {isPremium ? (
          <p className={styles.meta}>
            Tập premium cần thêm quyền MONETIZE; nếu thiếu, backend sẽ chặn bằng 403.
          </p>
        ) : null}
        {fieldErrors.length > 0 ? (
          <ul role="alert" className={styles.list}>
            {fieldErrors.map((code) => (
              <li key={code}>
                {code}: {VALIDATION_LABELS[code] ?? code}
              </li>
            ))}
          </ul>
        ) : null}
      </fieldset>

      <div className={styles.actions}>
        <Button variant="secondary" disabled={busy} onClick={() => void build(PRIVATE_KIND)}>
          Tạo archive riêng tư
        </Button>
        <Button
          variant="primary"
          disabled={blocked || busy}
          onClick={() => void build(PUBLICATION_KIND)}
        >
          Tạo bundle publication
        </Button>
      </div>

      {error ? (
        <p role="alert" className={styles.error}>
          Không tạo được bản xuất: {error}
        </p>
      ) : null}
      {notice ? (
        <p role="status" className={styles.success}>
          {notice}
        </p>
      ) : null}

      <p className={styles.notice} data-testid="manual-upload-notice">
        {MANUAL_UPLOAD_NOTICE}
      </p>

      {bundles.length > 0 ? (
        <div className={styles.bundles}>
          {bundles.map((bundle) => (
            <BundleCard
              key={bundle.id}
              bundle={bundle}
              busy={busy}
              onRebuild={() => void build(bundle.kind)}
            />
          ))}
        </div>
      ) : (
        <p className={styles.meta}>Chưa có bản xuất nào cho tập này.</p>
      )}
    </section>
  );
}

function BundleCard({
  bundle,
  busy,
  onRebuild,
}: {
  bundle: BundleStatus;
  busy: boolean;
  onRebuild: () => void;
}) {
  const groups = groupBundleFiles(bundle.files);

  return (
    <article aria-label={KIND_LABELS[bundle.kind] ?? bundle.kind} className={styles.card}>
      <div className={styles.cardHeader}>
        <strong>{KIND_LABELS[bundle.kind] ?? bundle.kind}</strong>
        <strong
          className={
            bundle.verified === false
              ? styles.statusBlocked
              : bundle.verified
                ? styles.statusOk
                : styles.statusMuted
          }
        >
          {bundle.verified === null
            ? 'Chưa xác minh lại'
            : bundle.verified
              ? 'Checksum khớp'
              : 'Checksum lệch'}
        </strong>
      </div>

      <p className={styles.meta}>
        manifestSha256:{' '}
        <code data-testid={`manifest-${bundle.id}`} className={styles.code}>
          {bundle.manifestSha256}
        </code>
      </p>
      {bundle.createdAt ? (
        <p className={styles.meta}>Tạo lúc: {bundle.createdAt}</p>
      ) : null}

      {bundle.mismatches.length > 0 ? (
        <div role="alert" className={styles.error}>
          <strong>File lệch hoặc thiếu checksum:</strong>
          <ul className={styles.list}>
            {bundle.mismatches.map((name) => (
              <li key={name}>{name}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {bundle.stale ? (
        <div role="alert" className={styles.staleBlock}>
          <strong className={styles.stale}>
            Bản xuất đã cũ (STALE) — hãy tạo lại trước khi dùng.
          </strong>
          <ul className={styles.list}>
            {bundle.staleReasons.map((reason) => (
              <li key={reason}>
                {reason}: {STALE_LABELS[reason] ?? 'Không rõ nguyên nhân'}
              </li>
            ))}
          </ul>
          <Button variant="secondary" disabled={busy} onClick={onRebuild}>
            Tạo lại bundle
          </Button>
        </div>
      ) : null}

      {FILE_GROUPS.filter((group) => groups[group.key]?.length).map((group) => (
        <div key={group.key} className={styles.fileGroup}>
          <h4 className={styles.fileKind}>{group.label}</h4>
          <ul className={styles.fileList}>
            {groups[group.key].map((name) => (
              <li key={name} className={styles.fileRow}>
                {name}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </article>
  );
}
