/**
 * U05: workspace tabs, dock and layout persistence (pure reducer).
 *
 * The layout is project-scoped: every action carries the project it was raised
 * for and any action for another project is rejected, so two projects can never
 * leak tabs into each other's workspace.
 *
 * Acceptance rules encoded here (implementation-plan.md U05):
 * - at most 8 tabs per pane; opening a 9th may evict a tab **only** when the
 *   evicted tab is clean (a dirty tab is never closed silently: the caller gets
 *   `TAB_LIMIT_DIRTY` and can offer "save then close" or let the user cancel);
 * - a dirty tab is not closed by `close` either (`TAB_DIRTY`) unless `force` is
 *   set — saving is what makes a tab closable;
 * - a failed/canceled save therefore leaves the layout exactly as it was
 *   (no action is applied until it is allowed);
 * - `undock` folds the secondary pane back into the primary one and refuses to
 *   drop tabs when that would overflow the limit;
 * - persisted layouts carry a version; an unknown version is migrated by
 *   falling back to the default layout (`migrated: true`) instead of throwing.
 */

export const LAYOUT_VERSION = 1;
export const MAX_TABS_PER_PANE = 8;

export type PaneId = 'primary' | 'secondary';

export type TabKind = 'EDITOR' | 'QA' | 'INSPECTOR' | 'DIFF' | 'JOB' | 'PREVIEW';

export type WorkspaceTab = {
  id: string;
  kind: TabKind;
  projectId: string;
  chapterId: string | null;
  title: string;
  dirty?: boolean;
};

export type PaneState = {
  tabs: WorkspaceTab[];
  activeTabId: string | null;
};

export type WorkspaceLayout = {
  version: number;
  projectId: string;
  panes: Record<PaneId, PaneState>;
  /** Docked panes are shown side by side; undocked shows the focused pane only. */
  docked: boolean;
  focus: PaneId;
};

export type LayoutAction =
  | { type: 'open'; projectId: string; tab: WorkspaceTab; pane?: PaneId }
  | { type: 'close'; projectId: string; tabId: string; force?: boolean }
  | { type: 'activate'; projectId: string; pane: PaneId; tabId: string }
  | { type: 'reorder'; projectId: string; pane: PaneId; tabId: string; toIndex: number }
  | { type: 'split'; projectId: string; tabId: string }
  | { type: 'dock'; projectId: string }
  | { type: 'undock'; projectId: string }
  | { type: 'markClean'; projectId: string; tabId: string }
  | { type: 'markDirty'; projectId: string; tabId: string }
  | { type: 'reset'; projectId: string };

export type LayoutResult = {
  layout: WorkspaceLayout;
  error?: string;
  evictedTabId?: string;
};

const PANES: PaneId[] = ['primary', 'secondary'];

export function emptyPane(): PaneState {
  return { tabs: [], activeTabId: null };
}

export function defaultLayout(projectId: string): WorkspaceLayout {
  return {
    version: LAYOUT_VERSION,
    projectId,
    panes: { primary: emptyPane(), secondary: emptyPane() },
    docked: false,
    focus: 'primary',
  };
}

export function applyLayoutAction(layout: WorkspaceLayout, action: LayoutAction): LayoutResult {
  if (action.projectId !== layout.projectId) {
    // Cross-project leakage guard: the action is ignored, the layout is untouched.
    return { layout, error: 'PROJECT_MISMATCH' };
  }
  switch (action.type) {
    case 'open':
      return openTab(layout, action.tab, action.pane ?? 'primary');
    case 'close':
      return closeTab(layout, action.tabId, action.force === true);
    case 'activate':
      return activateTab(layout, action.pane, action.tabId);
    case 'reorder':
      return reorderTab(layout, action.pane, action.tabId, action.toIndex);
    case 'split':
      return splitTab(layout, action.tabId);
    case 'dock':
      return { layout: { ...layout, docked: true } };
    case 'undock':
      return undock(layout);
    case 'markClean':
      return setDirty(layout, action.tabId, false);
    case 'markDirty':
      return setDirty(layout, action.tabId, true);
    case 'reset':
      return { layout: defaultLayout(layout.projectId) };
    default:
      return { layout, error: 'UNKNOWN_ACTION' };
  }
}

export function findTab(layout: WorkspaceLayout, tabId: string): { pane: PaneId; tab: WorkspaceTab } | null {
  for (const pane of PANES) {
    const tab = layout.panes[pane].tabs.find((item) => item.id === tabId);
    if (tab) {
      return { pane, tab };
    }
  }
  return null;
}

export function activeTab(layout: WorkspaceLayout, pane: PaneId): WorkspaceTab | null {
  const state = layout.panes[pane];
  return state.tabs.find((tab) => tab.id === state.activeTabId) ?? null;
}

export function tabCount(layout: WorkspaceLayout): number {
  return PANES.reduce((total, pane) => total + layout.panes[pane].tabs.length, 0);
}

export function serializeLayout(layout: WorkspaceLayout): string {
  return JSON.stringify({
    version: LAYOUT_VERSION,
    projectId: layout.projectId,
    panes: layout.panes,
    docked: layout.docked,
    focus: layout.focus,
  });
}

/**
 * Restore a persisted layout. Unknown/corrupt payloads and other projects fall
 * back to the default layout instead of failing the workspace (U05 migration).
 */
export function parseLayout(raw: string | null | undefined, projectId: string): { layout: WorkspaceLayout; migrated: boolean } {
  if (!raw) {
    return { layout: defaultLayout(projectId), migrated: false };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { layout: defaultLayout(projectId), migrated: true };
  }
  if (!isLayoutShape(parsed) || parsed.version !== LAYOUT_VERSION || parsed.projectId !== projectId) {
    return { layout: defaultLayout(projectId), migrated: true };
  }
  return {
    layout: {
      version: LAYOUT_VERSION,
      projectId,
      panes: {
        primary: sanitizePane(parsed.panes.primary, projectId),
        secondary: sanitizePane(parsed.panes.secondary, projectId),
      },
      docked: parsed.docked === true,
      focus: parsed.focus === 'secondary' ? 'secondary' : 'primary',
    },
    migrated: false,
  };
}

function openTab(layout: WorkspaceLayout, tab: WorkspaceTab, pane: PaneId): LayoutResult {
  if (tab.projectId !== layout.projectId) {
    return { layout, error: 'PROJECT_MISMATCH' };
  }
  const existing = findTab(layout, tab.id);
  if (existing) {
    // Re-opening a known tab just focuses it (never duplicates it).
    const next = activateTab(layout, existing.pane, tab.id);
    return { ...next, error: undefined };
  }
  const state = layout.panes[pane];
  if (state.tabs.length >= MAX_TABS_PER_PANE) {
    const victim = activeTab(layout, pane) ?? state.tabs[state.tabs.length - 1] ?? null;
    if (victim === null) {
      return { layout, error: 'TAB_LIMIT' };
    }
    if (victim.dirty) {
      // The 9th tab must not close unsaved work: the caller offers close-after-save.
      return { layout, error: 'TAB_LIMIT_DIRTY' };
    }
    const kept = state.tabs.filter((item) => item.id !== victim.id);
    return {
      layout: withPane(layout, pane, { tabs: [...kept, tab], activeTabId: tab.id }, pane),
      evictedTabId: victim.id,
    };
  }
  return {
    layout: withPane(layout, pane, { tabs: [...state.tabs, tab], activeTabId: tab.id }, pane),
  };
}

function closeTab(layout: WorkspaceLayout, tabId: string, force: boolean): LayoutResult {
  const found = findTab(layout, tabId);
  if (!found) {
    return { layout, error: 'TAB_NOT_FOUND' };
  }
  if (found.tab.dirty && !force) {
    return { layout, error: 'TAB_DIRTY' };
  }
  const state = layout.panes[found.pane];
  const index = state.tabs.findIndex((tab) => tab.id === tabId);
  const tabs = state.tabs.filter((tab) => tab.id !== tabId);
  const activeTabId =
    state.activeTabId === tabId ? (tabs[Math.max(0, index - 1)]?.id ?? tabs[0]?.id ?? null) : state.activeTabId;
  return { layout: withPane(layout, found.pane, { tabs, activeTabId }, layout.focus) };
}

function activateTab(layout: WorkspaceLayout, pane: PaneId, tabId: string): LayoutResult {
  const state = layout.panes[pane];
  if (!state.tabs.some((tab) => tab.id === tabId)) {
    return { layout, error: 'TAB_NOT_FOUND' };
  }
  return { layout: withPane(layout, pane, { tabs: state.tabs, activeTabId: tabId }, pane) };
}

function reorderTab(layout: WorkspaceLayout, pane: PaneId, tabId: string, toIndex: number): LayoutResult {
  const state = layout.panes[pane];
  const from = state.tabs.findIndex((tab) => tab.id === tabId);
  if (from === -1) {
    return { layout, error: 'TAB_NOT_FOUND' };
  }
  const target = Math.min(Math.max(toIndex, 0), state.tabs.length - 1);
  if (target === from) {
    return { layout };
  }
  const tabs = [...state.tabs];
  const [moved] = tabs.splice(from, 1);
  tabs.splice(target, 0, moved);
  return { layout: withPane(layout, pane, { tabs, activeTabId: state.activeTabId }, layout.focus) };
}

function splitTab(layout: WorkspaceLayout, tabId: string): LayoutResult {
  const found = findTab(layout, tabId);
  if (!found) {
    return { layout, error: 'TAB_NOT_FOUND' };
  }
  if (found.pane === 'secondary') {
    return { layout: { ...layout, docked: true, focus: 'secondary' } };
  }
  const secondary = layout.panes.secondary;
  if (secondary.tabs.length >= MAX_TABS_PER_PANE) {
    const victim = activeTab(layout, 'secondary') ?? secondary.tabs[secondary.tabs.length - 1] ?? null;
    if (victim?.dirty) {
      return { layout, error: 'TAB_LIMIT_DIRTY' };
    }
  }
  const primaryTabs = layout.panes.primary.tabs.filter((tab) => tab.id !== tabId);
  const primaryActive =
    layout.panes.primary.activeTabId === tabId
      ? (primaryTabs[primaryTabs.length - 1]?.id ?? null)
      : layout.panes.primary.activeTabId;
  const secondaryTabs = [
    ...secondary.tabs.filter((tab) => tab.id !== tabId).slice(-(MAX_TABS_PER_PANE - 1)),
    found.tab,
  ];
  const panes = {
    primary: { tabs: primaryTabs, activeTabId: primaryActive },
    secondary: { tabs: secondaryTabs, activeTabId: found.tab.id },
  };
  return { layout: { ...layout, panes, docked: true, focus: 'secondary' } };
}

function undock(layout: WorkspaceLayout): LayoutResult {
  const secondary = layout.panes.secondary.tabs;
  if (secondary.length === 0) {
    return { layout: { ...layout, docked: false, focus: 'primary' } };
  }
  if (layout.panes.primary.tabs.length + secondary.length > MAX_TABS_PER_PANE) {
    return { layout, error: 'UNDOCK_OVERFLOW' };
  }
  const panes = {
    primary: {
      tabs: [...layout.panes.primary.tabs, ...secondary],
      activeTabId: layout.panes.secondary.activeTabId ?? layout.panes.primary.activeTabId,
    },
    secondary: emptyPane(),
  };
  return { layout: { ...layout, panes, docked: false, focus: 'primary' } };
}

function setDirty(layout: WorkspaceLayout, tabId: string, dirty: boolean): LayoutResult {
  const found = findTab(layout, tabId);
  if (!found) {
    return { layout, error: 'TAB_NOT_FOUND' };
  }
  const state = layout.panes[found.pane];
  const tabs = state.tabs.map((tab) => (tab.id === tabId ? { ...tab, dirty } : tab));
  return { layout: withPane(layout, found.pane, { tabs, activeTabId: state.activeTabId }, layout.focus) };
}

function withPane(
  layout: WorkspaceLayout,
  pane: PaneId,
  state: PaneState,
  focus: PaneId,
): WorkspaceLayout {
  return { ...layout, panes: { ...layout.panes, [pane]: state }, focus };
}

function sanitizePane(value: unknown, projectId: string): PaneState {
  if (typeof value !== 'object' || value === null) {
    return emptyPane();
  }
  const candidate = value as { tabs?: unknown; activeTabId?: unknown };
  const tabs = Array.isArray(candidate.tabs)
    ? candidate.tabs.filter((tab): tab is WorkspaceTab => isTabShape(tab) && tab.projectId === projectId)
    : [];
  const activeTabId =
    typeof candidate.activeTabId === 'string' && tabs.some((tab) => tab.id === candidate.activeTabId)
      ? candidate.activeTabId
      : (tabs[0]?.id ?? null);
  return { tabs: tabs.slice(0, MAX_TABS_PER_PANE), activeTabId };
}

function isLayoutShape(value: unknown): value is WorkspaceLayout & { panes: Record<PaneId, PaneState> } {
  if (typeof value !== 'object' || value === null) {
    return false;
  }
  const candidate = value as { version?: unknown; projectId?: unknown; panes?: unknown };
  const panes = candidate.panes as Record<string, unknown> | undefined;
  return (
    typeof candidate.version === 'number' &&
    typeof candidate.projectId === 'string' &&
    typeof panes === 'object' &&
    panes !== null &&
    'primary' in panes &&
    'secondary' in panes
  );
}

function isTabShape(value: unknown): value is WorkspaceTab {
  if (typeof value !== 'object' || value === null) {
    return false;
  }
  const tab = value as Partial<WorkspaceTab>;
  return (
    typeof tab.id === 'string' &&
    typeof tab.kind === 'string' &&
    typeof tab.projectId === 'string' &&
    typeof tab.title === 'string' &&
    (tab.chapterId === null || typeof tab.chapterId === 'string')
  );
}
