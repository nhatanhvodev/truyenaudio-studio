import { useCallback, useEffect, useId, useMemo, useState } from 'react';
import { apiJson } from '../../shared/api';

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
  const fieldId = useId();
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
    <section aria-label="Xuất bản và metadata" data-testid="export-workflow" style={styles.shell}>
      <div style={styles.header}>
        <h2 style={styles.title}>Xuất bản</h2>
        {status ? (
          <span style={blocked ? styles.blockedPill : styles.readyPill}>
            {blocked ? 'Bị chặn xuất bản' : 'Sẵn sàng xuất bản'}
          </span>
        ) : (
          <span style={styles.mutedPill}>Đang kiểm tra quyền</span>
        )}
      </div>

      {status ? (
        <p style={styles.meta}>
          Hash đánh giá quyền:{' '}
          <code data-testid="rights-hash">{status.gate.rightsEvaluationHash.slice(0, 12)}…</code>
        </p>
      ) : null}

      {blocked ? (
        <ul aria-label="Lý do bị chặn" style={styles.reasons}>
          {status?.gate.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      ) : null}

      {loadError ? (
        <p role="alert" style={styles.error}>
          Không tải được trạng thái export: {loadError}
        </p>
      ) : null}

      <fieldset style={styles.fieldset}>
        <legend style={styles.legend}>Metadata tập</legend>
        <div style={styles.field}>
          <label htmlFor={`${fieldId}-title`}>Tiêu đề tập</label>
          <input
            id={`${fieldId}-title`}
            value={episodeTitle}
            onChange={(event) => setEpisodeTitle(event.target.value)}
            style={styles.input}
          />
        </div>
        <div style={styles.field}>
          <label htmlFor={`${fieldId}-number`}>Số tập</label>
          <input
            id={`${fieldId}-number`}
            type="number"
            min={1}
            value={episodeNumber}
            onChange={(event) => setEpisodeNumber(event.target.value)}
            style={styles.input}
          />
        </div>
        <label style={styles.checkbox}>
          <input
            type="checkbox"
            checked={isPremium}
            onChange={(event) => setIsPremium(event.target.checked)}
          />
          Tập premium
        </label>
        {isPremium ? (
          <p style={styles.meta}>
            Tập premium cần thêm quyền MONETIZE; nếu thiếu, backend sẽ chặn bằng 403.
          </p>
        ) : null}
        {fieldErrors.length > 0 ? (
          <ul role="alert" style={styles.fieldErrors}>
            {fieldErrors.map((code) => (
              <li key={code}>
                {code}: {VALIDATION_LABELS[code] ?? code}
              </li>
            ))}
          </ul>
        ) : null}
      </fieldset>

      <div style={styles.actions}>
        <button
          type="button"
          style={styles.secondaryButton}
          disabled={busy}
          onClick={() => void build(PRIVATE_KIND)}
        >
          Tạo archive riêng tư
        </button>
        <button
          type="button"
          style={blocked ? styles.disabledButton : styles.primaryButton}
          disabled={blocked || busy}
          onClick={() => void build(PUBLICATION_KIND)}
        >
          Tạo bundle publication
        </button>
      </div>

      {error ? (
        <p role="alert" style={styles.error}>
          Không tạo được bản xuất: {error}
        </p>
      ) : null}
      {notice ? (
        <p role="status" style={styles.success}>
          {notice}
        </p>
      ) : null}

      <p style={styles.notice} data-testid="manual-upload-notice">
        {MANUAL_UPLOAD_NOTICE}
      </p>

      {bundles.length > 0 ? (
        <div style={styles.bundles}>
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
        <p style={styles.meta}>Chưa có bản xuất nào cho tập này.</p>
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
    <article aria-label={KIND_LABELS[bundle.kind] ?? bundle.kind} style={styles.card}>
      <div style={styles.header}>
        <strong>{KIND_LABELS[bundle.kind] ?? bundle.kind}</strong>
        <span style={bundle.verified === false ? styles.blockedPill : styles.readyPill}>
          {bundle.verified === null
            ? 'Chưa xác minh lại'
            : bundle.verified
              ? 'Checksum khớp'
              : 'Checksum lệch'}
        </span>
      </div>

      <p style={styles.meta}>
        manifestSha256: <code data-testid={`manifest-${bundle.id}`}>{bundle.manifestSha256}</code>
      </p>
      {bundle.createdAt ? (
        <p style={styles.meta}>Tạo lúc: {bundle.createdAt}</p>
      ) : null}

      {bundle.mismatches.length > 0 ? (
        <div role="alert" style={styles.error}>
          <strong>File lệch hoặc thiếu checksum:</strong>
          <ul style={styles.list}>
            {bundle.mismatches.map((name) => (
              <li key={name}>{name}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {bundle.stale ? (
        <div role="alert" style={styles.stale}>
          <strong>Bản xuất đã cũ (STALE) — hãy tạo lại trước khi dùng.</strong>
          <ul style={styles.list}>
            {bundle.staleReasons.map((reason) => (
              <li key={reason}>
                {reason}: {STALE_LABELS[reason] ?? 'Không rõ nguyên nhân'}
              </li>
            ))}
          </ul>
          <button
            type="button"
            style={styles.secondaryButton}
            disabled={busy}
            onClick={onRebuild}
          >
            Tạo lại bundle
          </button>
        </div>
      ) : null}

      {FILE_GROUPS.filter((group) => groups[group.key]?.length).map((group) => (
        <div key={group.key} style={styles.fileGroup}>
          <h4 style={styles.groupTitle}>{group.label}</h4>
          <ul style={styles.list}>
            {groups[group.key].map((name) => (
              <li key={name}>{name}</li>
            ))}
          </ul>
        </div>
      ))}
    </article>
  );
}

const styles: Record<string, React.CSSProperties> = {
  shell: {
    color: '#17202a',
    fontFamily: 'Inter, Segoe UI, Arial, sans-serif',
    border: '1px solid #d9e1e8',
    borderRadius: 8,
    padding: 16,
    background: '#ffffff',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 12,
    flexWrap: 'wrap',
  },
  title: { margin: 0, fontSize: 18, letterSpacing: 0 },
  meta: { margin: '8px 0 0', color: '#52606d', fontSize: 13 },
  readyPill: {
    borderRadius: 999,
    padding: '5px 10px',
    background: '#e8f5ec',
    color: '#1c6638',
    fontWeight: 700,
    fontSize: 13,
  },
  blockedPill: {
    borderRadius: 999,
    padding: '5px 10px',
    background: '#fff4d6',
    color: '#7a4b00',
    fontWeight: 700,
    fontSize: 13,
  },
  mutedPill: {
    borderRadius: 999,
    padding: '5px 10px',
    background: '#eef2f6',
    color: '#52606d',
    fontWeight: 700,
    fontSize: 13,
  },
  reasons: { margin: '12px 0 0', paddingLeft: 18, color: '#7a2e0e' },
  fieldset: {
    margin: '16px 0 0',
    border: '1px solid #e3e9ef',
    borderRadius: 8,
    padding: 12,
  },
  legend: { padding: '0 6px', fontWeight: 700, fontSize: 14 },
  field: { display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 10 },
  input: {
    border: '1px solid #a9b7c6',
    borderRadius: 6,
    padding: '8px 10px',
    fontSize: 14,
    maxWidth: 320,
  },
  checkbox: { display: 'flex', alignItems: 'center', gap: 8, fontSize: 14 },
  fieldErrors: { margin: '10px 0 0', paddingLeft: 18, color: '#a03030' },
  actions: { display: 'flex', flexWrap: 'wrap', gap: 10, marginTop: 14 },
  primaryButton: {
    border: '1px solid #0b5cad',
    borderRadius: 6,
    padding: '8px 12px',
    background: '#0b5cad',
    color: '#ffffff',
    fontWeight: 700,
  },
  disabledButton: {
    border: '1px solid #c7d1da',
    borderRadius: 6,
    padding: '8px 12px',
    background: '#eef2f6',
    color: '#8794a1',
    fontWeight: 700,
  },
  secondaryButton: {
    border: '1px solid #a9b7c6',
    borderRadius: 6,
    padding: '8px 12px',
    background: '#ffffff',
    color: '#17324d',
    fontWeight: 700,
  },
  error: {
    margin: '12px 0 0',
    padding: 10,
    borderRadius: 6,
    background: '#fdecec',
    color: '#8a1f1f',
    fontSize: 14,
  },
  success: {
    margin: '12px 0 0',
    padding: 10,
    borderRadius: 6,
    background: '#e8f5ec',
    color: '#1c6638',
    fontSize: 14,
  },
  stale: {
    margin: '12px 0 0',
    padding: 10,
    borderRadius: 6,
    background: '#fff4d6',
    color: '#7a4b00',
    fontSize: 14,
  },
  notice: {
    margin: '14px 0 0',
    padding: '10px 12px',
    borderRadius: 6,
    background: '#eef4fb',
    color: '#17324d',
    fontSize: 13,
  },
  bundles: { display: 'flex', flexDirection: 'column', gap: 12, marginTop: 14 },
  card: { border: '1px solid #e3e9ef', borderRadius: 8, padding: 12 },
  fileGroup: { marginTop: 10 },
  groupTitle: { margin: '0 0 4px', fontSize: 13, textTransform: 'uppercase', color: '#52606d' },
  list: { margin: '6px 0 0', paddingLeft: 18, fontSize: 13 },
};
