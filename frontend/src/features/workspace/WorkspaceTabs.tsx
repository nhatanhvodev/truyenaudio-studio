import { useCallback, useEffect, useRef, useState } from 'react';

import { useWorkspaceLayout } from './useWorkspaceLayout';
import styles from './WorkspaceTabs.module.css';
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
 * Shared description for a tab holding unsaved editor content. A dirty tab
 * points at it with `aria-describedby`, never with extra text inside the button:
 * the accessible NAME of a tab is how the tab is addressed (`getByRole('tab',
 * { name })` in the unit and E2E suites both match on it exactly), so appending
 * a marker to the name would rename every tab that has unsaved work.
 *
 * The same string already reaches the DOM from the layout status line below, so
 * this adds no new user-visible wording.
 */
const DIRTY_HINT_ID = 'workspace-tab-dirty-hint';
const DIRTY_HINT_TEXT = 'có thay đổi chưa lưu';

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
    <section className={styles.shell} aria-label="Không gian làm việc">
      {/* Rendered once and referenced by id, so any number of dirty tabs can
          describe themselves from it without duplicating the id. */}
      <span id={DIRTY_HINT_ID} className="visually-hidden">
        {DIRTY_HINT_TEXT}
      </span>
      <div className={styles.row}>
        <div role="tablist" aria-label="Tab đang mở" onKeyDown={onKeyDown} className={styles.tablist}>
          {tabs.map((tab, index) => {
            const selected = tab.id === currentTabId;
            return (
              <span key={tab.id} className={styles.tabShell}>
                <button
                  type="button"
                  role="tab"
                  id={`tab-${tab.id}`}
                  aria-selected={selected}
                  aria-controls={`panel-${tab.id}`}
                  aria-describedby={tab.dirty ? DIRTY_HINT_ID : undefined}
                  tabIndex={index === focusIndex ? 0 : -1}
                  ref={(node) => {
                    tabRefs.current[tab.id] = node;
                  }}
                  onClick={() => activate(tab.id)}
                  className={styles.tab}
                >
                  {tab.title}
                  {/* Decorative: the cue is the marker's PRESENCE, not its
                      colour, and aria-hidden keeps it out of the accessible
                      name while aria-describedby above carries the meaning. */}
                  {tab.dirty ? <span aria-hidden="true" className={styles.dirtyMark} /> : null}
                </button>
                <button
                  type="button"
                  aria-label={`Đóng tab ${tab.title}`}
                  onClick={() => closeTab(tab.id)}
                  className={styles.close}
                >
                  ×
                </button>
              </span>
            );
          })}
          {tabs.length === 0 ? <span className={styles.empty}>Chưa mở tab nào</span> : null}
        </div>

        <div className={styles.row}>
          <span className={styles.count} aria-label="Số tab">
            {tabs.length}/{MAX_TABS_PER_PANE}
          </span>
          <button
            type="button"
            onClick={() =>
              dispatch({ type: layout.docked ? 'undock' : 'dock', projectId })
            }
            className={styles.secondary}
          >
            {layout.docked ? 'Bỏ dock' : 'Dock khung phụ'}
          </button>
          <button
            type="button"
            onClick={() => void store.save()}
            disabled={store.saving}
            className={styles.primary}
          >
            Lưu layout
          </button>
        </div>
      </div>

      {notice || store.error ? (
        <p role="alert" className={styles.error}>
          {notice || store.error}
          {notice === 'TAB_DIRTY' ? (
            <button
              type="button"
              onClick={() => {
                if (pendingCloseId) {
                  closeTab(pendingCloseId, true);
                }
              }}
              className={`${styles.secondary} ${styles.alertAction}`}
            >
              Đóng và bỏ thay đổi
            </button>
          ) : null}
        </p>
      ) : null}

      <p role="status" className={styles.meta}>
        Nháp layout: {store.revision === null ? 'chưa lưu' : `bản ${store.revision}`}
        {store.migrated ? ' · đã chuyển đổi từ phiên bản cũ' : ''}
        {store.dirty ? ' · có thay đổi chưa lưu' : ''}
      </p>

      {layout.docked ? (
        <section aria-label="Khung phụ" className={styles.dock}>
          {secondaryTabs.length === 0 ? (
            <p className={styles.empty}>Khung phụ trống — dock một tab để xem song song.</p>
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

      <div className={styles.actions}>
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
            className={styles.link}
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
