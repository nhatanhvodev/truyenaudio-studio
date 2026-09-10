import { useCallback, useEffect, useRef, useState } from 'react';

import { useWorkspaceLayout } from './useWorkspaceLayout';
import { MAX_TABS_PER_PANE, type TabKind, type WorkspaceTab } from './workspaceLayout';

export type WorkspaceTabDefinition = {
  id: string;
  kind: TabKind;
  title: string;
  chapterId: string | null;
  to: string;
};

type Props = {
  /** Project that owns the layout; persistence is disabled for the local bucket. */
  projectId: string;
  /** Routes the user has visited, in visit order (the parent owns navigation). */
  openTabs: WorkspaceTabDefinition[];
  /** Tab matching the current route. */
  currentTabId: string | null;
  /** Tabs with unsaved editor content: they are dirty and cannot be closed silently. */
  dirtyTabIds?: string[];
  onNavigate: (to: string) => void;
  /** Rendered inside the docked secondary pane. */
  renderSecondary?: (tab: WorkspaceTabDefinition) => React.ReactNode;
};

/**
 * U05 round 3: the actual tab/dock UI.
 *
 * The tab list is driven by the U05 layout store (order, limit, eviction and
 * conflict rules live in the reducer), the parent owns routing (`onNavigate`)
 * and this component adds the interaction layer:
 *
 * - WAI-ARIA tab keyboard support: Left/Right/Home/End move focus, Enter/Space
 *   activates, `Delete` closes (a dirty tab is refused with a reason and can be
 *   closed with the explicit "Đóng và bỏ thay đổi" action);
 * - dock/undock control with the reducer's overflow guard;
 * - the current route is always the active tab of the primary pane, so a
 *   keyboard user never loses their place.
 */
export function WorkspaceTabs({
  projectId,
  openTabs,
  currentTabId,
  dirtyTabIds = [],
  onNavigate,
  renderSecondary,
}: Props) {
  const store = useWorkspaceLayout({ projectId, enabled: projectId !== 'local' });
  const [focusIndex, setFocusIndex] = useState(0);
  const [notice, setNotice] = useState('');
  const [pendingCloseId, setPendingCloseId] = useState<string | null>(null);
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  /** Tabs the user closed on purpose: they are not re-opened by a route sync. */
  const closedRef = useRef<Set<string>>(new Set());

  const { dispatch, layout } = store;
  const dirtyKey = dirtyTabIds.join('|');
  const syncSignature = `${store.generation}|${currentTabId ?? ''}|${openTabs.map((tab) => tab.id).join(',')}|${dirtyKey}`;
  const lastSignature = useRef('');

  // Route-derived tabs are synchronised once per route/dirty change: `sync` opens
  // the missing tabs (order and the 8-tab limit are enforced by the reducer) and
  // activates the current route. Tabs the user closed stay closed.
  useEffect(() => {
    if (lastSignature.current === syncSignature) {
      return;
    }
    lastSignature.current = syncSignature;
    const dirty = new Set(dirtyKey ? dirtyKey.split('|') : []);
    if (currentTabId !== null) {
      // Navigating back to a tab the user closed re-opens it on purpose.
      closedRef.current.delete(currentTabId);
    }
    const tabsToSync = openTabs.filter(
      (definition) => !closedRef.current.has(definition.id) || definition.id === currentTabId,
    );
    dispatch({
      type: 'sync',
      projectId,
      tabs: tabsToSync.map((definition) =>
        toWorkspaceTab(projectId, definition, dirty.has(definition.id)),
      ),
      activeTabId: currentTabId,
    });
  }, [currentTabId, dirtyKey, dispatch, openTabs, projectId, syncSignature]);

  const tabs = layout.panes.primary.tabs;
  const secondaryTabs = layout.panes.secondary.tabs;

  // The roving tabindex follows the active tab only when the ACTIVE TAB changes.
  // Depending on focusIndex/tabs here made the effect fight the keyboard: after
  // ArrowLeft the DOM focus moved but the effect immediately restored focusIndex to
  // the active tab, so the next Enter re-activated the old tab (found by the browser
  // E2E in e2e/workspace-tabs.spec.ts).
  const tabsRef = useRef(tabs);
  tabsRef.current = tabs;

  useEffect(() => {
    if (currentTabId === null) {
      return;
    }
    const index = tabsRef.current.findIndex((tab) => tab.id === currentTabId);
    if (index >= 0) {
      setFocusIndex(index);
    }
  }, [currentTabId]);

  const focusTabAt = useCallback(
    (index: number) => {
      const bounded = Math.min(Math.max(index, 0), Math.max(tabs.length - 1, 0));
      setFocusIndex(bounded);
      const target = tabs[bounded];
      if (target) {
        tabRefs.current[target.id]?.focus();
      }
    },
    [tabs],
  );

  const definitionFor = useCallback(
    (tabId: string): WorkspaceTabDefinition | undefined => openTabs.find((item) => item.id === tabId),
    [openTabs],
  );

  /**
   * Route to open for a tab. A tab restored from the saved layout was not visited
   * in this session, so it has no route definition yet — the tab id *is* the
   * pathname, which keeps restored tabs navigable instead of dead.
   */
  const routeFor = useCallback(
    (tabId: string): string | null =>
      definitionFor(tabId)?.to ?? (tabId.startsWith('/') ? tabId : null),
    [definitionFor],
  );

  const activate = useCallback(
    (tabId: string) => {
      dispatch({ type: 'activate', projectId, pane: 'primary', tabId });
      const to = routeFor(tabId);
      if (to) {
        onNavigate(to);
      }
    },
    [dispatch, onNavigate, projectId, routeFor],
  );

  const closeTab = useCallback(
    (tabId: string, force = false) => {
      const definition = definitionFor(tabId);
      const dirty = dirtyTabIds.includes(tabId);
      if (!force && definition && dirty) {
        setNotice('TAB_DIRTY');
        setPendingCloseId(tabId);
        return;
      }
      const remaining = tabs.filter((tab) => tab.id !== tabId);
      const result = dispatch({ type: 'close', projectId, tabId, force });
      if (result.error) {
        setNotice(result.error);
        return;
      }
      closedRef.current.add(tabId);
      setNotice('');
      setPendingCloseId(null);
      if (tabId === currentTabId) {
        const next = remaining[Math.max(0, remaining.length - 1)];
        onNavigate((next ? routeFor(next.id) : null) ?? '/');
      }
    },
    [currentTabId, dirtyTabIds, dispatch, onNavigate, projectId, routeFor, tabs],
  );

  function onKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (tabs.length === 0) {
      return;
    }
    if (event.key === 'ArrowRight') {
      event.preventDefault();
      focusTabAt(focusIndex + 1);
    } else if (event.key === 'ArrowLeft') {
      event.preventDefault();
      focusTabAt(focusIndex - 1);
    } else if (event.key === 'Home') {
      event.preventDefault();
      focusTabAt(0);
    } else if (event.key === 'End') {
      event.preventDefault();
      focusTabAt(tabs.length - 1);
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      const tab = tabs[focusIndex];
      if (tab) {
        activate(tab.id);
      }
    } else if (event.key === 'Delete') {
      event.preventDefault();
      const tab = tabs[focusIndex];
      if (tab) {
        closeTab(tab.id);
      }
    }
  }

  return (
    <section style={styles.shell} aria-label="Không gian làm việc">
      <div style={styles.row}>
        <div role="tablist" aria-label="Tab đang mở" onKeyDown={onKeyDown} style={styles.tablist}>
          {tabs.map((tab, index) => {
            const selected = tab.id === currentTabId;
            return (
              <span key={tab.id} style={styles.tabShell}>
                <button
                  type="button"
                  role="tab"
                  id={`tab-${tab.id}`}
                  aria-selected={selected}
                  aria-controls={`panel-${tab.id}`}
                  tabIndex={index === focusIndex ? 0 : -1}
                  ref={(node) => {
                    tabRefs.current[tab.id] = node;
                  }}
                  onClick={() => activate(tab.id)}
                  style={{ ...styles.tab, ...(selected ? styles.tabActive : {}) }}
                >
                  {tab.title}
                </button>
                <button
                  type="button"
                  aria-label={`Đóng tab ${tab.title}`}
                  onClick={() => closeTab(tab.id)}
                  style={styles.close}
                >
                  ×
                </button>
              </span>
            );
          })}
          {tabs.length === 0 ? <span style={styles.empty}>Chưa mở tab nào</span> : null}
        </div>

        <div style={styles.row}>
          <span style={styles.count} aria-label="Số tab">
            {tabs.length}/{MAX_TABS_PER_PANE}
          </span>
          <button
            type="button"
            onClick={() =>
              dispatch({ type: layout.docked ? 'undock' : 'dock', projectId })
            }
            style={styles.secondary}
          >
            {layout.docked ? 'Bỏ dock' : 'Dock khung phụ'}
          </button>
          <button
            type="button"
            onClick={() => void store.save()}
            disabled={store.saving}
            style={styles.primary}
          >
            Lưu layout
          </button>
        </div>
      </div>

      {notice || store.error ? (
        <p role="alert" style={styles.error}>
          {notice || store.error}
          {notice === 'TAB_DIRTY' ? (
            <button
              type="button"
              onClick={() => {
                if (pendingCloseId) {
                  closeTab(pendingCloseId, true);
                }
              }}
              style={{ ...styles.secondary, marginLeft: 8 }}
            >
              Đóng và bỏ thay đổi
            </button>
          ) : null}
        </p>
      ) : null}

      <p role="status" style={styles.meta}>
        Nháp layout: {store.revision === null ? 'chưa lưu' : `bản ${store.revision}`}
        {store.migrated ? ' · đã chuyển đổi từ phiên bản cũ' : ''}
        {store.dirty ? ' · có thay đổi chưa lưu' : ''}
      </p>

      {layout.docked ? (
        <section aria-label="Khung phụ" style={styles.dock}>
          {secondaryTabs.length === 0 ? (
            <p style={styles.empty}>Khung phụ trống — dock một tab để xem song song.</p>
          ) : (
            secondaryTabs.map((tab) => {
              const definition = definitionFor(tab.id);
              return (
                <div key={tab.id}>
                  <strong>{tab.title}</strong>
                  {definition && renderSecondary ? renderSecondary(definition) : null}
                </div>
              );
            })
          )}
        </section>
      ) : null}

      <div style={styles.actions}>
        {tabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            onClick={() => {
              dispatch({ type: 'split', projectId, tabId: tab.id });
              const definition = definitionFor(tab.id);
              if (definition) {
                setNotice('');
              }
            }}
            style={styles.link}
          >
            Dock “{tab.title}”
          </button>
        ))}
      </div>
    </section>
  );
}

function toWorkspaceTab(
  projectId: string,
  definition: WorkspaceTabDefinition,
  dirty: boolean,
): WorkspaceTab {
  return {
    id: definition.id,
    kind: definition.kind,
    projectId,
    chapterId: definition.chapterId,
    title: definition.title,
    dirty,
  };
}

const styles: Record<string, React.CSSProperties> = {
  shell: { display: 'grid', gap: 8, padding: 12, border: '1px solid #d9e1ea', borderRadius: 8, background: '#ffffff' },
  row: { display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: 8 },
  tablist: { display: 'flex', flexWrap: 'wrap', gap: 4, alignItems: 'center' },
  tabShell: { display: 'inline-flex', alignItems: 'center', border: '1px solid #c8d1dc', borderRadius: 6, overflow: 'hidden' },
  tab: { border: 0, background: '#ffffff', padding: '6px 10px', fontWeight: 700, color: '#344054', cursor: 'pointer' },
  tabActive: { background: '#e8f0ff', color: '#155eef' },
  close: { border: 0, background: 'transparent', padding: '6px 8px', cursor: 'pointer', color: '#7a8699' },
  count: { fontSize: 12, fontWeight: 700, color: '#667085' },
  primary: { padding: '6px 12px', border: 0, borderRadius: 6, background: '#155eef', color: '#fff', fontWeight: 700 },
  secondary: { padding: '6px 12px', border: '1px solid #c8d1dc', borderRadius: 6, background: '#fff', color: '#18212f', fontWeight: 700 },
  link: { border: 0, background: 'transparent', color: '#155eef', fontWeight: 700, cursor: 'pointer', padding: 0 },
  error: { margin: 0, color: '#9a3412', fontWeight: 700 },
  meta: { margin: 0, fontSize: 12, color: '#667085' },
  empty: { color: '#667085', fontWeight: 700 },
  dock: { display: 'grid', gap: 8, padding: 10, border: '1px dashed #98a2b3', borderRadius: 6, background: '#f8fafc' },
  actions: { display: 'flex', flexWrap: 'wrap', gap: 12 },
};
