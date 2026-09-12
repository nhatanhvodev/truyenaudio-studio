import { useCallback, useEffect, useState } from 'react';

import { apiJson } from '../../shared/api';
import { Button } from '../../shared/ui';

import styles from './GlossaryManager.module.css';

export interface GlossaryEntryView {
  id: string;
  source_term: string;
  target_term: string;
  reading: string | null;
  category: string | null;
  gender: string | null;
  addressing_notes: string | null;
  is_locked: boolean;
  revision_no: number;
  description: string | null;
  forbidden_forms: string[];
  evidence: string | null;
  scope_from_ordinal: number | null;
  scope_to_ordinal: number | null;
}

interface GlossaryResponse {
  revision: { entries: GlossaryEntryView[]; sha256: string };
  affected_source_segment_ids?: string[];
  invalidated?: string[];
}

export interface AffectedSegmentView {
  segmentId: string;
  chapterId: string;
  ordinal: number;
  excerpt: string;
}

export interface GlossaryPreviewResponse {
  revisionPreviewSha256: string;
  changed: boolean;
  affectedSegments: AffectedSegmentView[];
  affectedCount: number;
  invalidatedRuns: string[];
  invalidatedCount: number;
  warnings: string[];
}

export interface GlossaryManagerProps {
  projectId: string;
}

interface Draft {
  sourceTerm: string;
  targetTerm: string;
  reading: string;
  category: string;
  gender: string;
  addressingNotes: string;
  isLocked: boolean;
  description: string;
  forbiddenForms: string;
  evidence: string;
  scopeFrom: string;
  scopeTo: string;
}

const EMPTY_DRAFT: Draft = {
  sourceTerm: '',
  targetTerm: '',
  reading: '',
  category: '',
  gender: '',
  addressingNotes: '',
  isLocked: false,
  description: '',
  forbiddenForms: '',
  evidence: '',
  scopeFrom: '',
  scopeTo: '',
};

export function parseForbiddenForms(value: string): string[] {
  const seen: string[] = [];
  for (const form of value.split(',')) {
    const cleaned = form.trim();
    if (cleaned && !seen.includes(cleaned)) {
      seen.push(cleaned);
    }
  }
  return seen;
}

export function validateDraft(draft: Draft): string {
  if (!draft.sourceTerm.trim()) {
    return 'GLOSSARY_SOURCE_TERM_REQUIRED';
  }
  if (!draft.targetTerm.trim()) {
    return 'GLOSSARY_TARGET_TERM_REQUIRED';
  }
  const from = draft.scopeFrom ? Number.parseInt(draft.scopeFrom, 10) : null;
  const to = draft.scopeTo ? Number.parseInt(draft.scopeTo, 10) : null;
  if (from !== null && to !== null && from > to) {
    return 'GLOSSARY_SCOPE_INVALID';
  }
  if (parseForbiddenForms(draft.forbiddenForms).includes(draft.targetTerm.trim())) {
    return 'GLOSSARY_CONFLICT_INTERNAL';
  }
  return '';
}

export function buildEntryPayload(draft: Draft): Record<string, unknown> {
  return {
    source_term: draft.sourceTerm.trim(),
    target_term: draft.targetTerm.trim(),
    reading: draft.reading.trim() || null,
    category: draft.category.trim() || null,
    gender: draft.gender.trim() || null,
    addressing_notes: draft.addressingNotes.trim() || null,
    is_locked: draft.isLocked,
    description: draft.description.trim() || null,
    forbidden_forms: parseForbiddenForms(draft.forbiddenForms),
    evidence: draft.evidence.trim() || null,
    scope_from_ordinal: draft.scopeFrom ? Number.parseInt(draft.scopeFrom, 10) : null,
    scope_to_ordinal: draft.scopeTo ? Number.parseInt(draft.scopeTo, 10) : null,
  };
}

export function GlossaryManager({ projectId }: GlossaryManagerProps) {
  const [entries, setEntries] = useState<GlossaryEntryView[]>([]);
  const [revisionHash, setRevisionHash] = useState('');
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [preview, setPreview] = useState<GlossaryPreviewResponse | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const payload = await apiJson<GlossaryResponse>(`/api/projects/${projectId}/glossary`);
      setEntries(payload.revision?.entries ?? []);
      setRevisionHash(payload.revision?.sha256 ?? '');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Không tải được glossary');
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    setPreview(null);
  }, [draft]);

  async function previewScope() {
    const invalid = validateDraft(draft);
    if (invalid) {
      setError(invalid);
      setPreview(null);
      return;
    }
    setPreviewing(true);
    setError('');
    try {
      const payload = await apiJson<GlossaryPreviewResponse>(
        `/api/projects/${projectId}/glossary/preview`,
        {
          method: 'POST',
          body: JSON.stringify(buildEntryPayload(draft)),
        },
      );
      setPreview(payload);
    } catch (caught) {
      setPreview(null);
      setError(
        caught instanceof Error
          ? `Không xem được phạm vi: ${caught.message}`
          : 'Không xem được phạm vi',
      );
    } finally {
      setPreviewing(false);
    }
  }

  async function save() {
    const invalid = validateDraft(draft);
    if (invalid) {
      setError(invalid);
      return;
    }
    setSaving(true);
    setError('');
    setStatus('');
    try {
      const payload = await apiJson<GlossaryResponse>(`/api/projects/${projectId}/glossary`, {
        method: 'POST',
        body: JSON.stringify(buildEntryPayload(draft)),
      });
      const affected = payload.affected_source_segment_ids?.length ?? 0;
      const invalidated = payload.invalidated?.length ?? 0;
      setStatus(
        `Đã lưu thuật ngữ. Revision ${(payload.revision?.sha256 ?? '').slice(0, 8)}… · ${affected} segment chứa thuật ngữ · ${invalidated} bản dịch bị đánh stale.`,
      );
      setDraft(EMPTY_DRAFT);
      setPreview(null);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Không lưu được thuật ngữ');
    } finally {
      setSaving(false);
    }
  }

  function scopeLabel(entry: GlossaryEntryView): string {
    if (entry.scope_from_ordinal === null && entry.scope_to_ordinal === null) {
      return 'toàn project';
    }
    const from = entry.scope_from_ordinal ?? 'đầu';
    const to = entry.scope_to_ordinal ?? 'cuối';
    return `chương ${from}–${to}`;
  }

  return (
    <section aria-labelledby="glossary-manager-heading" className={styles.shell}>
      <h2 id="glossary-manager-heading" className={styles.heading}>
        Glossary
      </h2>
      <p className={styles.note}>
        Thuật ngữ khóa là QA cứng; forbidden form bị chặn khi xuất hiện trong bản dịch. Sửa glossary chỉ đánh
        stale các chương thực sự chứa thuật ngữ trong scope.
      </p>

      {error ? (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      ) : null}
      {status ? (
        <p role="status" className={styles.success}>
          {status}
        </p>
      ) : null}

      <div className={styles.row}>
        <label className={styles.field}>
          Thuật ngữ gốc
          <input
            className={styles.control}
            value={draft.sourceTerm}
            onChange={(event) => setDraft({ ...draft, sourceTerm: event.target.value })}
          />
        </label>
        <label className={styles.field}>
          Bản dịch chuẩn
          <input
            className={styles.control}
            value={draft.targetTerm}
            onChange={(event) => setDraft({ ...draft, targetTerm: event.target.value })}
          />
        </label>
        <label className={styles.field}>
          Cách đọc
          <input
            className={styles.control}
            value={draft.reading}
            onChange={(event) => setDraft({ ...draft, reading: event.target.value })}
          />
        </label>
        <label className={`${styles.field} ${styles.inlineField}`}>
          <input
            type="checkbox"
            checked={draft.isLocked}
            onChange={(event) => setDraft({ ...draft, isLocked: event.target.checked })}
          />
          Khóa thuật ngữ
        </label>
        <label className={styles.field}>
          Forbidden form (phẩy)
          <input
            className={styles.control}
            value={draft.forbiddenForms}
            onChange={(event) => setDraft({ ...draft, forbiddenForms: event.target.value })}
          />
        </label>
        <label className={styles.field}>
          Scope từ chương
          <input
            className={styles.control}
            inputMode="numeric"
            value={draft.scopeFrom}
            onChange={(event) => setDraft({ ...draft, scopeFrom: event.target.value.replace(/[^0-9]/g, '') })}
          />
        </label>
        <label className={styles.field}>
          đến chương
          <input
            className={styles.control}
            inputMode="numeric"
            value={draft.scopeTo}
            onChange={(event) => setDraft({ ...draft, scopeTo: event.target.value.replace(/[^0-9]/g, '') })}
          />
        </label>
      </div>
      <label className={`${styles.field} ${styles.wideField}`}>
        Mô tả / bằng chứng
        <textarea
          className={styles.textarea}
          value={draft.description}
          onChange={(event) => setDraft({ ...draft, description: event.target.value })}
          rows={2}
        />
      </label>
      <div className={styles.actions}>
        <Button variant="secondary" disabled={previewing} onClick={() => void previewScope()}>
          {previewing ? 'Đang xem…' : 'Xem phạm vi'}
        </Button>
        <Button variant="primary" disabled={saving} onClick={() => void save()}>
          {saving ? 'Đang lưu…' : 'Lưu thuật ngữ'}
        </Button>
      </div>

      {preview ? (
        <div data-testid="scope-preview" role="status" className={styles.preview}>
          <strong>Phạm vi sẽ bị ảnh hưởng (chưa lưu)</strong>
          <p className={styles.previewSummary}>
            {preview.affectedCount} segment chứa thuật ngữ · {preview.invalidatedCount} bản dịch sẽ bị
            đánh stale
            {preview.changed ? '' : ' · thuật ngữ không thay đổi'}
          </p>
          {preview.warnings.length > 0 ? (
            <div className={styles.warnings}>
              Cảnh báo:
              <ul className={styles.warningList}>
                {preview.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            </div>
          ) : null}
          {preview.affectedSegments.length > 0 ? (
            <div className={styles.tableWrap}>
              <table className={styles.table}>
                <caption className={styles.caption}>Segment chứa thuật ngữ ({preview.affectedCount} tổng cộng)</caption>
                <thead>
                  <tr>
                    <th scope="col" className={styles.th}>Chương</th>
                    <th scope="col" className={styles.th}>Đoạn trích</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.affectedSegments.slice(0, 5).map((segment) => (
                    <tr key={segment.segmentId}>
                      <td className={styles.td}>{segment.ordinal}</td>
                      <td className={styles.td}>{segment.excerpt}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </div>
      ) : null}

      <h3 className={styles.subheading}>Thuật ngữ active</h3>
      {loading ? <p className={styles.meta}>Đang tải…</p> : null}
      {!loading && entries.length === 0 ? <p className={styles.meta}>Chưa có thuật ngữ nào.</p> : null}
      {entries.length > 0 ? (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <caption className={styles.caption}>Glossary active (revision {revisionHash.slice(0, 8)}…)</caption>
            <thead>
              <tr>
                <th scope="col" className={styles.th}>Gốc</th>
                <th scope="col" className={styles.th}>Bản dịch</th>
                <th scope="col" className={styles.th}>Khóa</th>
                <th scope="col" className={styles.th}>Scope</th>
                <th scope="col" className={styles.th}>Forbidden</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <tr key={entry.id}>
                  <td className={styles.td}>{entry.source_term}</td>
                  <td className={styles.td}>{entry.target_term}</td>
                  <td className={styles.td}>{entry.is_locked ? 'Có' : '—'}</td>
                  <td className={styles.td}>{scopeLabel(entry)}</td>
                  <td className={styles.td}>{entry.forbidden_forms.length > 0 ? entry.forbidden_forms.join(', ') : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
