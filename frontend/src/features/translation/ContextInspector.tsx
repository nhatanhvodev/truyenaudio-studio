import { useEffect, useState } from 'react';

import { apiJson } from '../../shared/api';
import { Drawer } from '../../shared/ui/Drawer';

type LockedRule = { sourceTerm: string; targetTerm: string; forbiddenForms: string[] };

type MemoryEntry = {
  id: string;
  entityKey: string;
  entityType: string;
  summary: string;
  validFromOrdinal: number;
  validToOrdinal: number | null;
  status: string;
  sourceRunId: string | null;
};

type CharacterItem = {
  characterId: string;
  revisionId: string;
  revisionNo: number;
  canonicalName: string;
  aliases: string[];
  entityType: string;
  status: string;
};

export type ContextTrace = {
  chapterId: string;
  projectId: string;
  ordinal: number;
  state: string;
  run: {
    id: string;
    status: string;
    model: string | null;
    providerProfileId: string | null;
    promptVersion: string;
    glossaryRevisionHash: string | null;
    storyMemoryRevisionHash: string | null;
    sourceRevisionId: string;
  } | null;
  glossary: { sha256: string; entryCount: number; lockedRules: LockedRule[] };
  memory: { sha256: string; entries: MemoryEntry[] };
  characters: CharacterItem[];
  stale: { glossary: boolean; memory: boolean };
};

type Props = {
  chapterId: string;
  /** Injected by tests; defaults to the shared API client. */
  loadTrace?: (chapterId: string) => Promise<ContextTrace>;
};

/**
 * U06 round 2: context inspector.
 *
 * Shows exactly what the latest run was allowed to use — the glossary revision
 * with the locked rules that are in scope for this chapter, the APPROVED story
 * memory entries, the active characters with their aliases — and warns when the
 * run was produced with an older context (the recorded hash no longer matches).
 * Raw identifiers (run id, memory ids, character revision ids, hashes) are only
 * shown in the details drawer, never inline in the working view.
 */
export default function ContextInspector({ chapterId, loadTrace }: Props) {
  const [trace, setTrace] = useState<ContextTrace | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [detailsOpen, setDetailsOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    const load = loadTrace ?? ((id: string) => apiJson<ContextTrace>(`/api/chapters/${id}/translation/context-trace`));
    load(chapterId)
      .then((payload) => {
        if (!isTrace(payload)) {
          // A wrong-shaped response must not render a half-empty inspector.
          throw new Error('CONTEXT_TRACE_INVALID');
        }
        if (!cancelled) {
          setTrace(payload);
        }
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : 'CONTEXT_TRACE_FAILED');
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [chapterId, loadTrace]);

  if (loading) {
    return <p role="status">Đang tải ngữ cảnh…</p>;
  }
  if (!trace) {
    return <p role="alert">{error || 'CONTEXT_TRACE_FAILED'}</p>;
  }

  const staleReason = [
    trace.stale.glossary ? 'glossary' : null,
    trace.stale.memory ? 'bộ nhớ truyện' : null,
  ].filter(Boolean);

  return (
    <aside aria-label="Context inspector" style={styles.shell}>
      <header style={styles.header}>
        <h3 style={styles.title}>Ngữ cảnh của lượt dịch</h3>
        <button type="button" onClick={() => setDetailsOpen(true)} style={styles.secondary}>
          Chi tiết ID
        </button>
      </header>

      {trace.run === null ? (
        <p style={styles.note}>Chương này chưa có lượt dịch nào.</p>
      ) : (
        <p style={styles.note} aria-label="Lượt dịch">
          {trace.run.status} · {trace.run.model ?? 'chưa rõ model'} · prompt {trace.run.promptVersion}
        </p>
      )}

      {staleReason.length > 0 ? (
        <p role="alert" style={styles.warning}>
          Ngữ cảnh đã thay đổi sau lượt dịch này ({staleReason.join(', ')}). Chạy lại nếu muốn dùng bản mới
          nhất.
        </p>
      ) : (
        <p role="status" style={styles.ok}>
          Ngữ cảnh khớp với lượt dịch gần nhất.
        </p>
      )}

      <section aria-label="Glossary đang áp dụng" style={styles.block}>
        <strong style={styles.blockTitle}>Glossary ({trace.glossary.entryCount} mục)</strong>
        {trace.glossary.lockedRules.length === 0 ? (
          <p style={styles.note}>Không có thuật ngữ khóa trong phạm vi chương này.</p>
        ) : (
          <ul style={styles.list}>
            {trace.glossary.lockedRules.map((rule) => (
              <li key={`${rule.sourceTerm}-${rule.targetTerm}`}>
                {rule.sourceTerm} → {rule.targetTerm}
                {rule.forbiddenForms.length > 0 ? ` (cấm: ${rule.forbiddenForms.join(', ')})` : ''}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-label="Bộ nhớ truyện đã duyệt" style={styles.block}>
        <strong style={styles.blockTitle}>Bộ nhớ truyện đã duyệt ({trace.memory.entries.length})</strong>
        {trace.memory.entries.length === 0 ? (
          <p style={styles.note}>Chưa có mục bộ nhớ nào được duyệt cho chương này.</p>
        ) : (
          <ul style={styles.list}>
            {trace.memory.entries.map((entry) => (
              <li key={entry.id}>
                {entry.entityKey} · {entry.summary}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-label="Nhân vật đang hoạt động" style={styles.block}>
        <strong style={styles.blockTitle}>Nhân vật ({trace.characters.length})</strong>
        {trace.characters.length === 0 ? (
          <p style={styles.note}>Chưa có nhân vật nào.</p>
        ) : (
          <ul style={styles.list}>
            {trace.characters.map((character) => (
              <li key={character.characterId}>
                {character.canonicalName}
                {character.aliases.length > 0 ? ` (${character.aliases.join(', ')})` : ''} · {character.status}
              </li>
            ))}
          </ul>
        )}
      </section>

      <Drawer open={detailsOpen} title="ID chi tiết (ngữ cảnh)" onClose={() => setDetailsOpen(false)}>
        <dl style={styles.details}>
          <dt>Chapter</dt>
          <dd><code>{trace.chapterId}</code></dd>
          <dt>Project</dt>
          <dd><code>{trace.projectId}</code></dd>
          <dt>Ordinal</dt>
          <dd>{trace.ordinal}</dd>
          {trace.run ? (
            <>
              <dt>Run</dt>
              <dd><code>{trace.run.id}</code></dd>
              <dt>Source revision</dt>
              <dd><code>{trace.run.sourceRevisionId}</code></dd>
              <dt>Provider profile</dt>
              <dd><code>{trace.run.providerProfileId ?? '—'}</code></dd>
              <dt>Glossary hash (run)</dt>
              <dd><code>{trace.run.glossaryRevisionHash ?? '—'}</code></dd>
              <dt>Memory hash (run)</dt>
              <dd><code>{trace.run.storyMemoryRevisionHash ?? '—'}</code></dd>
            </>
          ) : null}
          <dt>Glossary hash (hiện tại)</dt>
          <dd><code>{trace.glossary.sha256}</code></dd>
          <dt>Memory hash (hiện tại)</dt>
          <dd><code>{trace.memory.sha256}</code></dd>
          {trace.memory.entries.map((entry) => (
            <span key={entry.id}>
              <dt>Memory {entry.entityKey}</dt>
              <dd><code>{entry.id}</code></dd>
            </span>
          ))}
          {trace.characters.map((character) => (
            <span key={character.characterId}>
              <dt>Character {character.canonicalName}</dt>
              <dd><code>{character.revisionId}</code> (rev {character.revisionNo})</dd>
            </span>
          ))}
        </dl>
      </Drawer>
    </aside>
  );
}

function isTrace(value: unknown): value is ContextTrace {
  if (typeof value !== 'object' || value === null) {
    return false;
  }
  const candidate = value as Partial<ContextTrace>;
  return (
    typeof candidate.chapterId === 'string' &&
    typeof candidate.projectId === 'string' &&
    typeof candidate.ordinal === 'number' &&
    typeof candidate.glossary === 'object' &&
    candidate.glossary !== null &&
    Array.isArray((candidate.glossary as { lockedRules?: unknown }).lockedRules) &&
    typeof candidate.memory === 'object' &&
    candidate.memory !== null &&
    Array.isArray((candidate.memory as { entries?: unknown }).entries) &&
    Array.isArray(candidate.characters) &&
    typeof candidate.stale === 'object' &&
    candidate.stale !== null
  );
}

const styles: Record<string, React.CSSProperties> = {  shell: { display: 'grid', gap: 10, padding: 12, border: '1px solid #d9e1ea', borderRadius: 8, background: '#ffffff' },
  header: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 },
  title: { margin: 0, fontSize: 15 },
  note: { margin: 0, fontSize: 13, color: '#475467' },
  ok: { margin: 0, fontSize: 13, color: '#166534', fontWeight: 700 },
  warning: { margin: 0, fontSize: 13, color: '#92400e', fontWeight: 700 },
  block: { display: 'grid', gap: 4, padding: 8, border: '1px solid #e2e8f0', borderRadius: 6, background: '#f8fafc' },
  blockTitle: { fontSize: 13 },
  list: { margin: 0, paddingLeft: 18, fontSize: 13, lineHeight: 1.5, color: '#344054' },
  secondary: { padding: '6px 10px', border: '1px solid #c8d1dc', borderRadius: 6, background: '#ffffff', fontWeight: 700 },
  details: { display: 'grid', gridTemplateColumns: 'auto minmax(0, 1fr)', gap: '6px 12px', fontSize: 12, margin: 0 },
};
