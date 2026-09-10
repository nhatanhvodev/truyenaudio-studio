import { useCallback, useEffect, useRef, useState } from 'react';

import { apiJson } from '../../shared/api';

export type DraftContent = Record<string, string>;

type ServerDraft = {
  revision: number;
  content: DraftContent;
};

export type DraftConflict = {
  code: string;
  serverRevision: number | null;
  serverContent: DraftContent;
  localContent: DraftContent;
  changedSegmentIds: string[];
};

type Options = {
  chapterId: string;
  /** Base source revision of the draft; a null value disables the draft store. */
  baseRevisionId: string | null;
  /** Called with server text when a draft is restored (initial load, reload, overwrite). */
  onRestore?: (content: DraftContent) => void;
};

export type ChapterDraftState = {
  revision: number | null;
  content: DraftContent | null;
  loading: boolean;
  saving: boolean;
  message: string;
  error: string;
  conflict: DraftConflict | null;
  save: (content: DraftContent, expectedRevision?: number | null) => Promise<boolean>;
  overwrite: () => Promise<boolean>;
  reload: () => Promise<void>;
  useServerContent: () => void;
  clearConflict: () => void;
};

const CONFLICT_CODE = 'DRAFT_REVISION_CONFLICT';

function changedSegmentIds(server: DraftContent, local: DraftContent): string[] {
  const keys = new Set([...Object.keys(server), ...Object.keys(local)]);
  return [...keys].filter((key) => (server[key] ?? '') !== (local[key] ?? '')).sort();
}

function isSameContent(left: DraftContent, right: DraftContent): boolean {
  return changedSegmentIds(left, right).length === 0;
}

/**
 * U04 round 2: restore/save a segmented chapter draft with optimistic concurrency.
 *
 * - the draft is scoped to (chapter, base source revision) and carries a
 *   revision number used as the compare-and-swap expectation on save;
 * - a rejected save keeps the local text (never silently overwritten) and is
 *   surfaced as a conflict with the remaining segment differences;
 * - a save that lost the race but carries **identical** content is treated as a
 *   successful idempotent retry (the caller may retry after an unknown outcome);
 * - `overwrite` writes the local text with the server's current revision, and
 *   `useServerContent` throws the local text away in favour of the server text.
 */
export function useChapterDraft({ chapterId, baseRevisionId, onRestore }: Options): ChapterDraftState {
  const [revision, setRevision] = useState<number | null>(null);
  const [content, setContent] = useState<DraftContent | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [conflict, setConflict] = useState<DraftConflict | null>(null);

  const restoreRef = useRef(onRestore);
  restoreRef.current = onRestore;
  const revisionRef = useRef<number | null>(null);
  revisionRef.current = revision;

  const readServerDraft = useCallback(async (): Promise<ServerDraft | null> => {
    if (!baseRevisionId) {
      return null;
    }
    const payload = await apiJson<{ draft?: { revision: number; content: DraftContent } | null }>(
      `/api/chapters/${chapterId}/draft?baseRevisionId=${encodeURIComponent(baseRevisionId)}`,
    );
    const draft = payload.draft ?? null;
    return draft ? { revision: draft.revision, content: draft.content } : null;
  }, [baseRevisionId, chapterId]);

  const adopt = useCallback((draft: ServerDraft | null): void => {
    setRevision(draft ? draft.revision : null);
    setContent(draft ? draft.content : {});
    if (draft) {
      restoreRef.current?.(draft.content);
    }
  }, []);

  useEffect(() => {
    if (!baseRevisionId) {
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError('');
    readServerDraft()
      .then((draft) => {
        if (!cancelled) {
          adopt(draft);
        }
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : 'DRAFT_LOAD_FAILED');
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
  }, [baseRevisionId, readServerDraft, adopt]);

  const save = useCallback(
    async (localContent: DraftContent, expectedRevision?: number | null): Promise<boolean> => {
      if (!baseRevisionId) {
        return false;
      }
      const expectation = expectedRevision === undefined ? revisionRef.current : expectedRevision;
      setSaving(true);
      setMessage('');
      setError('');
      try {
        const payload = await apiJson<{ draft: { revision: number; content: DraftContent } }>(
          `/api/chapters/${chapterId}/draft`,
          {
            method: 'PUT',
            body: {
              base_revision_id: baseRevisionId,
              content: localContent,
              expected_revision: expectation,
            },
          },
        );
        setRevision(payload.draft.revision);
        setContent(payload.draft.content);
        revisionRef.current = payload.draft.revision;
        setConflict(null);
        setMessage('Đã lưu nháp');
        return true;
      } catch (reason: unknown) {
        const code = reason instanceof Error ? reason.message : 'DRAFT_SAVE_FAILED';
        if (code !== CONFLICT_CODE) {
          // Unknown outcome (network/5xx): keep the text so the user can retry.
          setError(code);
          return false;
        }
        // Read the server draft without adopting it: the local text stays in the
        // editor until the user picks a side.
        const server = await readServerDraft().catch(() => null);
        const serverContent = server?.content ?? {};
        if (isSameContent(serverContent, localContent)) {
          // The write actually landed (or is already stored): idempotent retry.
          setRevision(server?.revision ?? revisionRef.current);
          revisionRef.current = server?.revision ?? revisionRef.current;
          setConflict(null);
          setMessage('Nháp đã đồng bộ với máy chủ');
          return true;
        }
        setConflict({
          code,
          serverRevision: server?.revision ?? null,
          serverContent,
          localContent,
          changedSegmentIds: changedSegmentIds(serverContent, localContent),
        });
        setError('Bản nháp trên máy chủ đã thay đổi');
        return false;
      } finally {
        setSaving(false);
      }
    },
    [baseRevisionId, chapterId, readServerDraft],
  );

  const overwrite = useCallback(async (): Promise<boolean> => {
    if (!conflict) {
      return false;
    }
    const localContent = conflict.localContent;
    const serverRevision = conflict.serverRevision;
    setConflict(null);
    return save(localContent, serverRevision);
  }, [conflict, save]);

  const reload = useCallback(async (): Promise<void> => {
    setConflict(null);
    setError('');
    try {
      adopt(await readServerDraft());
      setMessage('Đã tải lại nháp từ máy chủ');
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : 'DRAFT_LOAD_FAILED');
    }
  }, [adopt, readServerDraft]);

  const useServerContent = useCallback((): void => {
    if (conflict) {
      setContent(conflict.serverContent);
      setRevision(conflict.serverRevision);
      revisionRef.current = conflict.serverRevision;
      restoreRef.current?.(conflict.serverContent);
    }
    setConflict(null);
    setError('');
  }, [conflict]);

  const clearConflict = useCallback((): void => {
    setConflict(null);
    setError('');
  }, []);

  return {
    revision,
    content,
    loading,
    saving,
    message,
    error,
    conflict,
    save,
    overwrite,
    reload,
    useServerContent,
    clearConflict,
  };
}
