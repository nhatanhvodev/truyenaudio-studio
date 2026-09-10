import { useCallback, useEffect, useState } from 'react';

import { apiJson } from '../../shared/api';

export interface CharacterView {
  character_id: string;
  revision_id: string;
  revision_no: number;
  canonical_name: string;
  aliases: string[];
  entity_type: string;
  role: string | null;
  gender: string | null;
  status: string;
  evidence_source_revision_id: string | null;
  evidence_segment_ids: string[];
}

export interface RelationshipView {
  id: string;
  from_character_id: string;
  to_character_id: string;
  from_ordinal: number;
  to_ordinal: number | null;
  addressing: Record<string, string>;
  status: string;
}

export interface CharacterManagerProps {
  projectId: string;
}

export function parseList(value: string): string[] {
  const seen: string[] = [];
  for (const item of value.split(',')) {
    const cleaned = item.trim();
    if (cleaned && !seen.includes(cleaned)) {
      seen.push(cleaned);
    }
  }
  return seen;
}

export function parseAddressing(value: string): Record<string, string> {
  const result: Record<string, string> = {};
  for (const line of value.split('\n')) {
    const [key, ...rest] = line.split('=');
    const name = key?.trim();
    const target = rest.join('=').trim();
    if (name && target) {
      result[name] = target;
    }
  }
  return result;
}

export function CharacterManager({ projectId }: CharacterManagerProps) {
  const [characters, setCharacters] = useState<CharacterView[]>([]);
  const [relationships, setRelationships] = useState<RelationshipView[]>([]);
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState({
    canonicalName: '',
    entityType: 'PERSON',
    aliases: '',
    role: '',
    gender: '',
  });
  const [evidence, setEvidence] = useState<Record<string, { revisionId: string; segmentIds: string }>>({});
  const [relationshipDraft, setRelationshipDraft] = useState({
    fromId: '',
    toId: '',
    fromOrdinal: '1',
    toOrdinal: '',
    addressing: '',
  });
  const [ordinalFilter, setOrdinalFilter] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const payload = await apiJson<{ characters: CharacterView[] }>(
        `/api/projects/${projectId}/characters`,
      );
      setCharacters(payload.characters ?? []);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Không tải được nhân vật');
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function createCharacter() {
    if (!draft.canonicalName.trim()) {
      setError('CHARACTER_NAME_REQUIRED');
      return;
    }
    setBusy(true);
    setError('');
    setStatus('');
    try {
      await apiJson(`/api/projects/${projectId}/characters`, {
        method: 'POST',
        body: JSON.stringify({
          canonical_name: draft.canonicalName.trim(),
          entity_type: draft.entityType,
          aliases: parseList(draft.aliases),
          role: draft.role.trim() || null,
          gender: draft.gender.trim() || null,
        }),
      });
      setStatus('Đã tạo candidate; cần evidence để approve.');
      setDraft({ canonicalName: '', entityType: 'PERSON', aliases: '', role: '', gender: '' });
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Không tạo được nhân vật');
    } finally {
      setBusy(false);
    }
  }

  async function approve(characterId: string) {
    const record = evidence[characterId];
    const segmentIds = record ? parseList(record.segmentIds) : [];
    if (!record?.revisionId.trim() || segmentIds.length === 0) {
      setError('CHARACTER_EVIDENCE_REQUIRED');
      return;
    }
    setBusy(true);
    setError('');
    setStatus('');
    try {
      await apiJson(`/api/projects/${projectId}/characters/${characterId}/approve`, {
        method: 'POST',
        body: JSON.stringify({
          evidence_source_revision_id: record.revisionId.trim(),
          evidence_segment_ids: segmentIds,
        }),
      });
      setStatus('Đã approve nhân vật với evidence.');
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Không approve được nhân vật');
    } finally {
      setBusy(false);
    }
  }

  async function addRelationship() {
    setBusy(true);
    setError('');
    setStatus('');
    try {
      await apiJson(`/api/projects/${projectId}/characters/relationships`, {
        method: 'POST',
        body: JSON.stringify({
          from_character_id: relationshipDraft.fromId.trim(),
          to_character_id: relationshipDraft.toId.trim(),
          from_ordinal: Number.parseInt(relationshipDraft.fromOrdinal, 10) || 1,
          to_ordinal: relationshipDraft.toOrdinal ? Number.parseInt(relationshipDraft.toOrdinal, 10) : null,
          addressing: parseAddressing(relationshipDraft.addressing),
        }),
      });
      setStatus('Đã thêm quan hệ xưng hô có hướng.');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Không thêm được quan hệ');
    } finally {
      setBusy(false);
    }
  }

  async function loadRelationships(ordinal: string) {
    setOrdinalFilter(ordinal);
    const parsed = Number.parseInt(ordinal, 10);
    if (!Number.isFinite(parsed) || parsed < 1) {
      setRelationships([]);
      return;
    }
    try {
      const payload = await apiJson<{ relationships: RelationshipView[] }>(
        `/api/projects/${projectId}/characters/relationships?ordinal=${parsed}`,
      );
      setRelationships(payload.relationships ?? []);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Không tải được quan hệ');
    }
  }

  return (
    <section aria-labelledby="character-manager-heading">
      <h2 id="character-manager-heading" style={{ margin: '0 0 8px', fontSize: 15 }}>
        Nhân vật &amp; xưng hô
      </h2>
      <p style={{ margin: '0 0 8px', color: '#4b5563' }}>
        Nhân vật không bị gộp chỉ vì trùng tên; approve bắt buộc có evidence (source revision + segment). Giới
        tính thiếu giữ “chưa rõ”, không tự đoán.
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

      {loading ? <p>Đang tải…</p> : null}
      {!loading && characters.length === 0 ? <p>Chưa có nhân vật nào.</p> : null}
      {characters.length > 0 ? (
        <table>
          <caption>Nhân vật theo revision</caption>
          <thead>
            <tr>
              <th scope="col">Tên</th>
              <th scope="col">Alias</th>
              <th scope="col">Giới tính</th>
              <th scope="col">Trạng thái</th>
              <th scope="col">Evidence &amp; approve</th>
            </tr>
          </thead>
          <tbody>
            {characters.map((character) => (
              <tr key={character.character_id}>
                <td>{character.canonical_name}</td>
                <td>{character.aliases.length > 0 ? character.aliases.join(', ') : '—'}</td>
                <td>{character.gender ?? 'chưa rõ'}</td>
                <td>{character.status}</td>
                <td>
                  <input
                    aria-label={`Source revision cho ${character.canonical_name}`}
                    value={evidence[character.character_id]?.revisionId ?? ''}
                    onChange={(event) =>
                      setEvidence((previous) => ({
                        ...previous,
                        [character.character_id]: {
                          revisionId: event.target.value,
                          segmentIds: previous[character.character_id]?.segmentIds ?? '',
                        },
                      }))
                    }
                  />
                  <input
                    aria-label={`Segment ids cho ${character.canonical_name}`}
                    value={evidence[character.character_id]?.segmentIds ?? ''}
                    onChange={(event) =>
                      setEvidence((previous) => ({
                        ...previous,
                        [character.character_id]: {
                          revisionId: previous[character.character_id]?.revisionId ?? '',
                          segmentIds: event.target.value,
                        },
                      }))
                    }
                  />
                  <button type="button" disabled={busy} onClick={() => void approve(character.character_id)}>
                    Approve
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}

      <h3 style={{ fontSize: 14, margin: '16px 0 4px' }}>Thêm nhân vật (candidate)</h3>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <label>
          Tên chuẩn
          <input
            value={draft.canonicalName}
            onChange={(event) => setDraft({ ...draft, canonicalName: event.target.value })}
          />
        </label>
        <label>
          Loại
          <select
            value={draft.entityType}
            onChange={(event) => setDraft({ ...draft, entityType: event.target.value })}
          >
            <option value="PERSON">PERSON</option>
            <option value="ORGANIZATION">ORGANIZATION</option>
            <option value="OTHER">OTHER</option>
          </select>
        </label>
        <label>
          Alias (phẩy)
          <input value={draft.aliases} onChange={(event) => setDraft({ ...draft, aliases: event.target.value })} />
        </label>
        <label>
          Role
          <input value={draft.role} onChange={(event) => setDraft({ ...draft, role: event.target.value })} />
        </label>
        <label>
          Giới tính (để trống = chưa rõ)
          <select value={draft.gender} onChange={(event) => setDraft({ ...draft, gender: event.target.value })}>
            <option value="">chưa rõ</option>
            <option value="MALE">MALE</option>
            <option value="FEMALE">FEMALE</option>
            <option value="UNKNOWN">UNKNOWN</option>
          </select>
        </label>
        <button type="button" disabled={busy} onClick={() => void createCharacter()}>
          Tạo candidate
        </button>
      </div>

      <h3 style={{ fontSize: 14, margin: '16px 0 4px' }}>Quan hệ xưng hô có hướng</h3>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <label>
          Từ character id
          <input
            value={relationshipDraft.fromId}
            onChange={(event) => setRelationshipDraft({ ...relationshipDraft, fromId: event.target.value })}
          />
        </label>
        <label>
          Đến character id
          <input
            value={relationshipDraft.toId}
            onChange={(event) => setRelationshipDraft({ ...relationshipDraft, toId: event.target.value })}
          />
        </label>
        <label>
          Từ chương
          <input
            inputMode="numeric"
            value={relationshipDraft.fromOrdinal}
            onChange={(event) =>
              setRelationshipDraft({ ...relationshipDraft, fromOrdinal: event.target.value.replace(/[^0-9]/g, '') })
            }
          />
        </label>
        <label>
          Đến chương (trống = mở)
          <input
            inputMode="numeric"
            value={relationshipDraft.toOrdinal}
            onChange={(event) =>
              setRelationshipDraft({ ...relationshipDraft, toOrdinal: event.target.value.replace(/[^0-9]/g, '') })
            }
          />
        </label>
        <label>
          Addressing (mỗi dòng key=value)
          <textarea
            value={relationshipDraft.addressing}
            onChange={(event) => setRelationshipDraft({ ...relationshipDraft, addressing: event.target.value })}
            rows={2}
          />
        </label>
        <button type="button" disabled={busy} onClick={() => void addRelationship()}>
          Thêm quan hệ
        </button>
      </div>

      <label>
        Xem quan hệ tại chương
        <input
          inputMode="numeric"
          value={ordinalFilter}
          onChange={(event) => void loadRelationships(event.target.value.replace(/[^0-9]/g, ''))}
        />
      </label>
      {relationships.length > 0 ? (
        <ul aria-label="Quan hệ tại chương đã chọn">
          {relationships.map((relationship) => (
            <li key={relationship.id}>
              {relationship.from_character_id} → {relationship.to_character_id} (chương{' '}
              {relationship.from_ordinal}
              {relationship.to_ordinal ? `–${relationship.to_ordinal}` : '+'}):{' '}
              {Object.entries(relationship.addressing)
                .map(([key, value]) => `${key}=${value}`)
                .join(', ') || 'chưa có addressing'}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
