import { useCallback, useEffect, useState } from 'react';

import { apiJson } from '../../shared/api';

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
    <section aria-labelledby="glossary-manager-heading">
      <h2 id="glossary-manager-heading" style={{ margin: '0 0 8px', fontSize: 15 }}>
        Glossary
      </h2>
      <p style={{ margin: '0 0 8px', color: '#4b5563' }}>
        Thuật ngữ khóa là QA cứng; forbidden form bị chặn khi xuất hiện trong bản dịch. Sửa glossary chỉ đánh
        stale các chương thực sự chứa thuật ngữ trong scope.
      </p>

      {error ? (
        <p role="alert" style={{ color: '#b91c1c' }}>
          {error}
        </p>
      ) : null}
      {status ? (
        <p role="status" style={{ color: '#166534' }}>
          {status}
        </p>
      ) : null}

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <label>
          Thuật ngữ gốc
          <input
            value={draft.sourceTerm}
            onChange={(event) => setDraft({ ...draft, sourceTerm: event.target.value })}
          />
        </label>
        <label>
          Bản dịch chuẩn
          <input
            value={draft.targetTerm}
            onChange={(event) => setDraft({ ...draft, targetTerm: event.target.value })}
          />
        </label>
        <label>
          Cách đọc
          <input value={draft.reading} onChange={(event) => setDraft({ ...draft, reading: event.target.value })} />
        </label>
        <label>
          <input
            type="checkbox"
            checked={draft.isLocked}
            onChange={(event) => setDraft({ ...draft, isLocked: event.target.checked })}
          />
          Khóa thuật ngữ
        </label>
        <label>
          Forbidden form (phẩy)
          <input
            value={draft.forbiddenForms}
            onChange={(event) => setDraft({ ...draft, forbiddenForms: event.target.value })}
          />
        </label>
        <label>
          Scope từ chương
          <input
            inputMode="numeric"
            value={draft.scopeFrom}
            onChange={(event) => setDraft({ ...draft, scopeFrom: event.target.value.replace(/[^0-9]/g, '') })}
          />
        </label>
        <label>
          đến chương
          <input
            inputMode="numeric"
            value={draft.scopeTo}
            onChange={(event) => setDraft({ ...draft, scopeTo: event.target.value.replace(/[^0-9]/g, '') })}
          />
        </label>
      </div>
      <label style={{ display: 'block', marginTop: 8 }}>
        Mô tả / bằng chứng
        <textarea
          value={draft.description}
          onChange={(event) => setDraft({ ...draft, description: event.target.value })}
          rows={2}
          style={{ width: '100%' }}
        />
      </label>
      <button type="button" disabled={saving} onClick={() => void save()}>
        {saving ? 'Đang lưu…' : 'Lưu thuật ngữ'}
      </button>

      <h3 style={{ fontSize: 14, margin: '16px 0 4px' }}>Thuật ngữ active</h3>
      {loading ? <p>Đang tải…</p> : null}
      {!loading && entries.length === 0 ? <p>Chưa có thuật ngữ nào.</p> : null}
      {entries.length > 0 ? (
        <table>
          <caption>Glossary active (revision {revisionHash.slice(0, 8)}…)</caption>
          <thead>
            <tr>
              <th scope="col">Gốc</th>
              <th scope="col">Bản dịch</th>
              <th scope="col">Khóa</th>
              <th scope="col">Scope</th>
              <th scope="col">Forbidden</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((entry) => (
              <tr key={entry.id}>
                <td>{entry.source_term}</td>
                <td>{entry.target_term}</td>
                <td>{entry.is_locked ? 'Có' : '—'}</td>
                <td>{scopeLabel(entry)}</td>
                <td>{entry.forbidden_forms.length > 0 ? entry.forbidden_forms.join(', ') : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </section>
  );
}
