import { useCallback, useEffect, useRef, useState } from 'react';

import { apiJson } from '../../shared/api';

import {
  applyLayoutAction,
  defaultLayout,
  parseLayout,
  serializeLayout,
  type LayoutAction,
  type LayoutResult,
  type WorkspaceLayout,
} from './workspaceLayout';

type LayoutPayload = {
  projectId: string;
  layout: unknown | null;
  revision: number | null;
  migrated: boolean;
};

export type WorkspaceLayoutState = {
  layout: WorkspaceLayout;
  revision: number | null;
  loading: boolean;
  saving: boolean;
  dirty: boolean;
  migrated: boolean;
  error: string;
  conflict: boolean;
  /** Increments whenever a layout is adopted from the server or a reload. */
  generation: number;
  dispatch: (action: LayoutAction) => LayoutResult;
  save: () => Promise<boolean>;
  reloadServer: () => Promise<void>;
};

/**
 * U05 round 2: keep the workspace layout in sync with the per-project API.
 *
 * - the persisted layout is loaded once per project and sanitized through the
 *   same reducer helpers as the local state (foreign tabs of another project
 *   are dropped, the pane limit is enforced);
 * - a stored layout the client does not understand (`migrated: true`) falls
 *   back to the default layout instead of failing the workspace;
 * - every reducer action is applied locally first (instant feedback) and the
 *   document is persisted with compare-and-swap on `revision`; a 409 keeps the
 *   local layout and raises `conflict` so the caller can reload or overwrite.
 */
export function useWorkspaceLayout({
  projectId,
  enabled = true,
}: {
  projectId: string;
  enabled?: boolean;
}): WorkspaceLayoutState {
  const [layout, setLayout] = useState<WorkspaceLayout>(() => defaultLayout(projectId));
  const [revision, setRevision] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [migrated, setMigrated] = useState(false);
  const [error, setError] = useState('');
  const [conflict, setConflict] = useState(false);
  const [generation, setGeneration] = useState(0);

  const revisionRef = useRef<number | null>(null);
  revisionRef.current = revision;
  const savedRef = useRef<string>(serializeLayout(defaultLayout(projectId)));

  const adopt = useCallback((payload: LayoutPayload): WorkspaceLayout => {
    const restored = payload.layout
      ? parseLayout(JSON.stringify(payload.layout), projectId)
      : { layout: defaultLayout(projectId), migrated: false };
    savedRef.current = serializeLayout(restored.layout);
    setLayout(restored.layout);
    setRevision(payload.revision);
    revisionRef.current = payload.revision;
    setMigrated(payload.migrated || restored.migrated);
    setDirty(false);
    setConflict(false);
    setGeneration((current) => current + 1);
    return restored.layout;
  }, [projectId]);

  useEffect(() => {
    setLayout(defaultLayout(projectId));
    setRevision(null);
    revisionRef.current = null;
    setMigrated(false);
    setDirty(false);
    setConflict(false);
    setError('');
    if (!enabled) {
      return;
    }
    let cancelled = false;
    setLoading(true);
    apiJson<LayoutPayload>(`/api/projects/${projectId}/workspace-layout`)
      .then((payload) => {
        if (!cancelled) {
          adopt(payload);
        }
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : 'LAYOUT_LOAD_FAILED');
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
  }, [adopt, enabled, projectId]);

  const dispatch = useCallback(
    (action: LayoutAction): LayoutResult => {
      const result = applyLayoutAction(layout, action);
      if (result.error) {
        // A refused action leaves the layout exactly as it was.
        setError(result.error);
        return result;
      }
      setError('');
      setLayout(result.layout);
      setDirty(serializeLayout(result.layout) !== savedRef.current);
      return result;
    },
    [layout],
  );

  const save = useCallback(async (): Promise<boolean> => {
    setSaving(true);
    setError('');
    try {
      const payload = await apiJson<LayoutPayload>(`/api/projects/${projectId}/workspace-layout`, {
        method: 'PUT',
        body: { layout, expected_revision: revisionRef.current },
      });
      adopt(payload);
      return true;
    } catch (reason: unknown) {
      const code = reason instanceof Error ? reason.message : 'LAYOUT_SAVE_FAILED';
      setError(code);
      if (code === 'LAYOUT_REVISION_CONFLICT') {
        setConflict(true);
      }
      return false;
    } finally {
      setSaving(false);
    }
  }, [adopt, layout, projectId]);

  const reloadServer = useCallback(async (): Promise<void> => {
    try {
      const payload = await apiJson<LayoutPayload>(`/api/projects/${projectId}/workspace-layout`);
      adopt(payload);
      setError('');
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : 'LAYOUT_LOAD_FAILED');
    }
  }, [adopt, projectId]);

  return {
    layout,
    revision,
    loading,
    saving,
    dirty,
    migrated,
    error,
    conflict,
    generation,
    dispatch,
    save,
    reloadServer,
  };
}
