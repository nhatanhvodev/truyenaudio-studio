import { useCallback, useEffect, useState } from 'react';

import { apiJson } from '../../shared/api';

export interface MemoryEntryView {
  id: string;
  entity_key: string;
  entity_type: string;
  summary: string;
  valid_from_ordinal: number;
  valid_to_ordinal: number | null;
  revision_no: number;
  status: string;
  source_run_id: string | null;
  evidence_segment_ids: string[];
}

export interface MemoryManagerProps {
  projectId: string;
}

export function MemoryManager({ projectId }: MemoryManagerProps) {
  const [candidates, setCandidates] = useState<MemoryEntryView[]>([]);
  const [contextEntries, setContextEntries] = useState<MemoryEntryView[]>([]);
  const [contextHash, setContextHash] = useState('');
  const [ordinal, setOrdinal] = useState('');
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [evidence, setEvidence] = useState<Record<string, { runId: string; segmentIds: string }>>({});
  const [draft, setDraft] = useState({
    entityKey: '',
    entityType: 'character',
    summary: '',
    validFrom: '1',
    validTo: '',
  });

  const loadCandidates = useCallback(async () => {
    setError('');
    try {
      const payload = await apiJson<{ candidates: MemoryEntryView[] }>(
        `/api/projects/${projectId}/memory/candidates`,
      );
      setCandidates(payload.candidates ?? []);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Không tải được candidate summary');
    }
  }, [projectId]);

  useEffect(() => {
    void loadCandidates();
  }, [loadCandidates]);

  async function loadContext(value: string) {
    setOrdinal(value);
    const parsed = Number.parseInt(value, 10);
    if (!Number.isFinite(parsed) || parsed < 1) {
      setContextEntries([]);
      setContextHash('');
      return;
    }
    try {
      const payload = await apiJson<{ entries: MemoryEntryView[]; sha256: string }>(
        `/api/projects/${projectId}/memory/context?ordinal=${parsed}`,
      );
      setContextEntries(payload.entries ?? []);
      setContextHash(payload.sha256 ?? '');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Không tải được context');
    }
  }

  async function createCandidate() {
    if (!draft.entityKey.trim() || !draft.summary.trim()) {
      setError('MEMORY_ENTITY_REQUIRED');
      return;
    }
    setBusy(true);
    setError('');
    setStatus('');
    try {
      await apiJson(`/api/projects/${projectId}/memory/candidates`, {
        method: 'POST',
        body: JSON.stringify({
          entity_key: draft.entityKey.trim(),
          entity_type: draft.entityType,
          summary: draft.summary.trim(),
          valid_from_ordinal: Number.parseInt(draft.validFrom, 10) || 1,
          valid_to_ordinal: draft.validTo ? Number.parseInt(draft.validTo, 10) : null,
        }),
      });
      setStatus('Đã tạo candidate; cần duyệt trước khi dùng làm context.');
      setDraft({ entityKey: '', entityType: 'character', summary: '', validFrom: '1', validTo: '' });
      await loadCandidates();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Không tạo được candidate');
    } finally {
      setBusy(false);
    }
  }

  async function approve(memoryId: string) {
    const record = evidence[memoryId];
    const segmentIds = (record?.segmentIds ?? '')
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean);
    if (!record?.runId.trim() || segmentIds.length === 0) {
      setError('MEMORY_EVIDENCE_SEGMENTS_REQUIRED');
      return;
    }
    setBusy(true);
    setError('');
    setStatus('');
    try {
      await apiJson(`/api/projects/${projectId}/memory/${memoryId}/approve`, {
        method: 'POST',
        body: JSON.stringify({
          source_run_id: record.runId.trim(),
          evidence_segment_ids: segmentIds,
        }),
      });
      setStatus('Đã duyệt summary với evidence run + segment.');
      await loadCandidates();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Không duyệt được summary');
    } finally {
      setBusy(false);
    }
  }

  async function reject(memoryId: string) {
    setBusy(true);
    setError('');
    setStatus('');
    try {
      await apiJson(`/api/projects/${projectId}/memory/${memoryId}/reject`, { method: 'POST' });
      setStatus('Đã loại candidate.');
      await loadCandidates();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Không loại được candidate');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="memory-manager-heading">
      <h2 id="memory-manager-heading" style={{ margin: '0 0 8px', fontSize: 15 }}>
        Story memory
      </h2>
      <p style={{ margin: '0 0 8px', color: '#4b5563' }}>
        Chỉ summary/fact đã duyệt mới vào context; candidate không bao giờ được dùng làm ngữ cảnh dịch.
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

      <h3 style={{ fontSize: 14, margin: '12px 0 4px' }}>Candidate chờ duyệt</h3>
      {candidates.length === 0 ? <p>Không có candidate nào.</p> : null}
      {candidates.length > 0 ? (
        <table>
          <caption>Candidate summary/fact</caption>
          <thead>
            <tr>
              <th scope="col">Entity</th>
              <th scope="col">Summary</th>
              <th scope="col">Hiệu lực</th>
              <th scope="col">Evidence &amp; duyệt</th>
            </tr>
          </thead>
          <tbody>
            {candidates.map((candidate) => (
              <tr key={candidate.id}>
                <td>{candidate.entity_key}</td>
                <td>{candidate.summary}</td>
                <td>
                  từ chương {candidate.valid_from_ordinal}
                  {candidate.valid_to_ordinal ? `–${candidate.valid_to_ordinal}` : '+'}
                </td>
                <td>
                  <input
                    aria-label={`Run id cho ${candidate.entity_key}`}
                    value={evidence[candidate.id]?.runId ?? ''}
                    onChange={(event) =>
                      setEvidence((previous) => ({
                        ...previous,
                        [candidate.id]: {
                          runId: event.target.value,
                          segmentIds: previous[candidate.id]?.segmentIds ?? '',
                        },
                      }))
                    }
                  />
                  <input
                    aria-label={`Segment ids cho ${candidate.entity_key}`}
                    value={evidence[candidate.id]?.segmentIds ?? ''}
                    onChange={(event) =>
                      setEvidence((previous) => ({
                        ...previous,
                        [candidate.id]: {
                          runId: previous[candidate.id]?.runId ?? '',
                          segmentIds: event.target.value,
                        },
                      }))
                    }
                  />
                  <button type="button" disabled={busy} onClick={() => void approve(candidate.id)}>
                    Duyệt
                  </button>
                  <button type="button" disabled={busy} onClick={() => void reject(candidate.id)}>
                    Loại
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}

      <h3 style={{ fontSize: 14, margin: '12px 0 4px' }}>Tạo candidate</h3>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <label>
          Entity key
          <input
            value={draft.entityKey}
            onChange={(event) => setDraft({ ...draft, entityKey: event.target.value })}
          />
        </label>
        <label>
          Loại
          <select
            value={draft.entityType}
            onChange={(event) => setDraft({ ...draft, entityType: event.target.value })}
          >
            <option value="character">character</option>
            <option value="fact">fact</option>
            <option value="chapter">chapter</option>
          </select>
        </label>
        <label>
          Hiệu lực từ chương
          <input
            inputMode="numeric"
            value={draft.validFrom}
            onChange={(event) => setDraft({ ...draft, validFrom: event.target.value.replace(/[^0-9]/g, '') })}
          />
        </label>
        <label>
          đến chương
          <input
            inputMode="numeric"
            value={draft.validTo}
            onChange={(event) => setDraft({ ...draft, validTo: event.target.value.replace(/[^0-9]/g, '') })}
          />
        </label>
      </div>
      <label style={{ display: 'block', marginTop: 8 }}>
        Summary
        <textarea
          value={draft.summary}
          onChange={(event) => setDraft({ ...draft, summary: event.target.value })}
          rows={2}
          style={{ width: '100%' }}
        />
      </label>
      <button type="button" disabled={busy} onClick={() => void createCandidate()}>
        Tạo candidate
      </button>

      <h3 style={{ fontSize: 14, margin: '12px 0 4px' }}>Context đã duyệt</h3>
      <label>
        Xem context tại chương
        <input
          inputMode="numeric"
          value={ordinal}
          onChange={(event) => void loadContext(event.target.value.replace(/[^0-9]/g, ''))}
        />
      </label>
      {contextHash ? (
        <p>
          Revision hash: <code>{contextHash.slice(0, 12)}…</code>
        </p>
      ) : null}
      {contextEntries.length > 0 ? (
        <ul aria-label="Context đã duyệt">
          {contextEntries.map((entry) => (
            <li key={entry.id}>
              {entry.entity_key}: {entry.summary}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
