import { useEffect, useState } from 'react';

import { apiJson } from '../../shared/api';
import { Drawer } from '../../shared/ui/Drawer';

import styles from './ContextInspector.module.css';

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
    <aside aria-label="Context inspector" className={styles.shell}>
      <header className={styles.header}>
        <h3 className={styles.title}>Ngữ cảnh của lượt dịch</h3>
        <button type="button" onClick={() => setDetailsOpen(true)} className={styles.secondaryButton}>
          Chi tiết ID
        </button>
      </header>

      {trace.run === null ? (
        <p className={styles.note}>Chương này chưa có lượt dịch nào.</p>
      ) : (
        <p className={styles.note} aria-label="Lượt dịch">
          {trace.run.status} · {trace.run.model ?? 'chưa rõ model'} · prompt {trace.run.promptVersion}
        </p>
      )}

      {staleReason.length > 0 ? (
        <p role="alert" className={styles.stale}>
          Ngữ cảnh đã thay đổi sau lượt dịch này ({staleReason.join(', ')}). Chạy lại nếu muốn dùng bản mới
          nhất.
        </p>
      ) : (
        <p role="status" className={styles.ok}>
          Ngữ cảnh khớp với lượt dịch gần nhất.
        </p>
      )}

      <section aria-label="Glossary đang áp dụng" className={styles.group}>
        <strong className={styles.groupTitle}>Glossary ({trace.glossary.entryCount} mục)</strong>
        {trace.glossary.lockedRules.length === 0 ? (
          <p className={styles.note}>Không có thuật ngữ khóa trong phạm vi chương này.</p>
        ) : (
          <ul className={styles.list}>
            {trace.glossary.lockedRules.map((rule) => (
              <li key={`${rule.sourceTerm}-${rule.targetTerm}`} className={styles.entry}>
                {rule.sourceTerm} → {rule.targetTerm}
                {rule.forbiddenForms.length > 0 ? ` (cấm: ${rule.forbiddenForms.join(', ')})` : ''}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-label="Bộ nhớ truyện đã duyệt" className={styles.group}>
        <strong className={styles.groupTitle}>Bộ nhớ truyện đã duyệt ({trace.memory.entries.length})</strong>
        {trace.memory.entries.length === 0 ? (
          <p className={styles.note}>Chưa có mục bộ nhớ nào được duyệt cho chương này.</p>
        ) : (
          <ul className={styles.list}>
            {trace.memory.entries.map((entry) => (
              <li key={entry.id} className={styles.entry}>
                {entry.entityKey} · {entry.summary}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-label="Nhân vật đang hoạt động" className={styles.group}>
        <strong className={styles.groupTitle}>Nhân vật ({trace.characters.length})</strong>
        {trace.characters.length === 0 ? (
          <p className={styles.note}>Chưa có nhân vật nào.</p>
        ) : (
          <ul className={styles.list}>
            {trace.characters.map((character) => (
              <li key={character.characterId} className={styles.entry}>
                {character.canonicalName}
                {character.aliases.length > 0 ? ` (${character.aliases.join(', ')})` : ''} · {character.status}
              </li>
            ))}
          </ul>
        )}
      </section>

      <Drawer open={detailsOpen} title="ID chi tiết (ngữ cảnh)" onClose={() => setDetailsOpen(false)}>
        <dl className={styles.details}>
          <dt>Chapter</dt>
          <dd><code className={styles.rawId}>{trace.chapterId}</code></dd>
          <dt>Project</dt>
          <dd><code className={styles.rawId}>{trace.projectId}</code></dd>
          <dt>Ordinal</dt>
          <dd>{trace.ordinal}</dd>
          {trace.run ? (
            <>
              <dt>Run</dt>
              <dd><code className={styles.rawId}>{trace.run.id}</code></dd>
              <dt>Source revision</dt>
              <dd><code className={styles.rawId}>{trace.run.sourceRevisionId}</code></dd>
              <dt>Provider profile</dt>
              <dd><code className={styles.rawId}>{trace.run.providerProfileId ?? '—'}</code></dd>
              <dt>Glossary hash (run)</dt>
              <dd><code className={styles.rawId}>{trace.run.glossaryRevisionHash ?? '—'}</code></dd>
              <dt>Memory hash (run)</dt>
              <dd><code className={styles.rawId}>{trace.run.storyMemoryRevisionHash ?? '—'}</code></dd>
            </>
          ) : null}
          <dt>Glossary hash (hiện tại)</dt>
          <dd><code className={styles.rawId}>{trace.glossary.sha256}</code></dd>
          <dt>Memory hash (hiện tại)</dt>
          <dd><code className={styles.rawId}>{trace.memory.sha256}</code></dd>
          {trace.memory.entries.map((entry) => (
            <span key={entry.id}>
              <dt>Memory {entry.entityKey}</dt>
              <dd><code className={styles.rawId}>{entry.id}</code></dd>
            </span>
          ))}
          {trace.characters.map((character) => (
            <span key={character.characterId}>
              <dt>Character {character.canonicalName}</dt>
              <dd><code className={styles.rawId}>{character.revisionId}</code> (rev {character.revisionNo})</dd>
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
