import { describe, expect, it } from 'vitest';

import {
  MAX_TABS_PER_PANE,
  activeTab,
  applyLayoutAction,
  defaultLayout,
  findTab,
  parseLayout,
  serializeLayout,
  tabCount,
  type LayoutAction,
  type WorkspaceLayout,
  type WorkspaceTab,
} from './workspaceLayout';

const PROJECT = 'project-1';

function tab(id: string, overrides: Partial<WorkspaceTab> = {}): WorkspaceTab {
  return {
    id,
    kind: 'EDITOR',
    projectId: PROJECT,
    chapterId: `chapter-${id}`,
    title: `Tab ${id}`,
    ...overrides,
  };
}

function reduce(layout: WorkspaceLayout, action: LayoutAction) {
  const result = applyLayoutAction(layout, action);
  return result;
}

function open(layout: WorkspaceLayout, id: string, pane?: 'primary' | 'secondary') {
  return reduce(layout, {
    type: 'open',
    projectId: PROJECT,
    tab: tab(id),
    ...(pane ? { pane } : {}),
  });
}

function openMany(count: number, start = 0): WorkspaceLayout {
  let layout = defaultLayout(PROJECT);
  for (let index = start; index < start + count; index += 1) {
    layout = open(layout, `t${index}`).layout;
  }
  return layout;
}

describe('workspace layout reducer (U05)', () => {
  it('opens, activates and reorders tabs within a pane', () => {
    let layout = openMany(3);

    expect(layout.panes.primary.tabs.map((item) => item.id)).toEqual(['t0', 't1', 't2']);
    expect(layout.panes.primary.activeTabId).toBe('t2');
    expect(layout.focus).toBe('primary');

    const activated = reduce(layout, { type: 'activate', projectId: PROJECT, pane: 'primary', tabId: 't0' });
    expect(activated.layout.panes.primary.activeTabId).toBe('t0');

    const reordered = reduce(activated.layout, {
      type: 'reorder',
      projectId: PROJECT,
      pane: 'primary',
      tabId: 't2',
      toIndex: 0,
    });
    expect(reordered.layout.panes.primary.tabs.map((item) => item.id)).toEqual(['t2', 't0', 't1']);
    expect(reordered.layout.panes.primary.activeTabId).toBe('t0');
  });

  it('re-opening a known tab focuses it instead of duplicating it', () => {
    const layout = openMany(2);

    const again = open(layout, 't0');

    expect(again.layout.panes.primary.tabs).toHaveLength(2);
    expect(again.layout.panes.primary.activeTabId).toBe('t0');
  });

  it('refuses the 9th tab when the tab it would evict has unsaved changes', () => {
    let layout = openMany(MAX_TABS_PER_PANE);
    layout = reduce(layout, { type: 'markDirty', projectId: PROJECT, tabId: `t${MAX_TABS_PER_PANE - 1}` }).layout;

    const refused = open(layout, 'extra');

    expect(refused.error).toBe('TAB_LIMIT_DIRTY');
    expect(tabCount(refused.layout)).toBe(MAX_TABS_PER_PANE);
    expect(findTab(refused.layout, `t${MAX_TABS_PER_PANE - 1}`)).not.toBeNull();
    expect(findTab(refused.layout, 'extra')).toBeNull();
  });

  it('evicts the active clean tab for the 9th and reports which tab was evicted', () => {
    const layout = openMany(MAX_TABS_PER_PANE);

    const opened = open(layout, 'extra');

    expect(opened.error).toBeUndefined();
    expect(opened.evictedTabId).toBe(`t${MAX_TABS_PER_PANE - 1}`);
    expect(tabCount(opened.layout)).toBe(MAX_TABS_PER_PANE);
    expect(findTab(opened.layout, 'extra')).not.toBeNull();
  });

  it('keeps a dirty tab when closing is attempted, and closes it once saved', () => {
    let layout = openMany(2);
    layout = reduce(layout, { type: 'markDirty', projectId: PROJECT, tabId: 't1' }).layout;

    const blocked = reduce(layout, { type: 'close', projectId: PROJECT, tabId: 't1' });
    expect(blocked.error).toBe('TAB_DIRTY');
    expect(findTab(blocked.layout, 't1')).not.toBeNull();

    // A canceled save leaves the layout untouched (nothing was applied).
    expect(findTab(layout, 't1')?.tab.dirty).toBe(true);

    // After a successful save the tab is clean and can be closed.
    const saved = reduce(layout, { type: 'markClean', projectId: PROJECT, tabId: 't1' }).layout;
    const closed = reduce(saved, { type: 'close', projectId: PROJECT, tabId: 't1' });
    expect(closed.error).toBeUndefined();
    expect(findTab(closed.layout, 't1')).toBeNull();
    expect(closed.layout.panes.primary.activeTabId).toBe('t0');
  });

  it('supports explicit-force close for a dirty tab', () => {
    let layout = openMany(1);
    layout = reduce(layout, { type: 'markDirty', projectId: PROJECT, tabId: 't0' }).layout;

    const closed = reduce(layout, { type: 'close', projectId: PROJECT, tabId: 't0', force: true });

    expect(closed.error).toBeUndefined();
    expect(tabCount(closed.layout)).toBe(0);
    expect(closed.layout.panes.primary.activeTabId).toBeNull();
  });

  it('splits a tab into the docked secondary pane and folds it back on undock', () => {
    let layout = openMany(3);

    const split = reduce(layout, { type: 'split', projectId: PROJECT, tabId: 't1' });
    expect(split.error).toBeUndefined();
    expect(split.layout.docked).toBe(true);
    expect(split.layout.focus).toBe('secondary');
    expect(split.layout.panes.primary.tabs.map((item) => item.id)).toEqual(['t0', 't2']);
    expect(split.layout.panes.secondary.tabs.map((item) => item.id)).toEqual(['t1']);
    expect(activeTab(split.layout, 'secondary')?.id).toBe('t1');

    const folded = reduce(split.layout, { type: 'undock', projectId: PROJECT });
    expect(folded.error).toBeUndefined();
    expect(folded.layout.docked).toBe(false);
    expect(folded.layout.panes.primary.tabs.map((item) => item.id)).toEqual(['t0', 't2', 't1']);
    expect(folded.layout.panes.secondary.tabs).toEqual([]);
  });

  it('refuses undock when folding would overflow the pane limit', () => {
    let layout = openMany(MAX_TABS_PER_PANE);
    layout = open(layout, 'side', 'secondary').layout;

    const refused = reduce(layout, { type: 'undock', projectId: PROJECT });

    expect(refused.error).toBe('UNDOCK_OVERFLOW');
    expect(refused.layout.panes.secondary.tabs).toHaveLength(1);
  });

  it('ignores actions from another project so tabs never leak across projects', () => {
    const layout = openMany(1);

    const foreign = reduce(layout, {
      type: 'open',
      projectId: 'project-2',
      tab: tab('other', { projectId: 'project-2' }),
    });
    expect(foreign.error).toBe('PROJECT_MISMATCH');
    expect(tabCount(foreign.layout)).toBe(1);

    const foreignTab = open(layout, 'x');
    expect(foreignTab.error).toBeUndefined();
    const mixed = reduce(layout, { type: 'open', projectId: PROJECT, tab: tab('y', { projectId: 'project-2' }) });
    expect(mixed.error).toBe('PROJECT_MISMATCH');
  });

  it('syncs route tabs: opens only the missing ones and activates the current route', () => {
    let layout = openMany(2);
    layout = reduce(layout, { type: 'markDirty', projectId: PROJECT, tabId: 't0' }).layout;

    const synced = reduce(layout, {
      type: 'sync',
      projectId: PROJECT,
      tabs: [tab('t0'), tab('t1'), tab('route')],
      activeTabId: 'route',
    });

    // Known tabs are untouched (dirty state preserved), only the new one is added.
    expect(synced.layout.panes.primary.tabs.map((item) => item.id)).toEqual(['t0', 't1', 'route']);
    expect(findTab(synced.layout, 't0')?.tab.dirty).toBe(true);
    expect(synced.layout.panes.primary.activeTabId).toBe('route');
    expect(synced.error).toBeUndefined();
  });

  it('sync ignores foreign tabs and does not reorder or close anything', () => {
    const layout = openMany(3);

    const synced = reduce(layout, {
      type: 'sync',
      projectId: PROJECT,
      tabs: [tab('t1'), tab('foreign', { projectId: 'project-2' }), tab('t0')],
      activeTabId: 't1',
    });

    expect(synced.error).toBe('PROJECT_MISMATCH');
    expect(synced.layout.panes.primary.tabs.map((item) => item.id)).toEqual(['t0', 't1', 't2']);
    expect(synced.layout.panes.primary.activeTabId).toBe('t1');
  });

  it('resets to the default layout for the project', () => {
    const layout = openMany(4);

    const reset = reduce(layout, { type: 'reset', projectId: PROJECT });

    expect(reset.layout).toEqual(defaultLayout(PROJECT));
  });

  it('round-trips a layout and drops foreign or unknown-version payloads', () => {
    const layout = openMany(2);
    const raw = serializeLayout(layout);

    const restored = parseLayout(raw, PROJECT);
    expect(restored.migrated).toBe(false);
    expect(restored.layout.panes.primary.tabs.map((item) => item.id)).toEqual(['t0', 't1']);

    const otherProject = parseLayout(raw, 'project-2');
    expect(otherProject.migrated).toBe(true);
    expect(tabCount(otherProject.layout)).toBe(0);

    const oldVersion = parseLayout(JSON.stringify({ ...JSON.parse(raw), version: 0 }), PROJECT);
    expect(oldVersion.migrated).toBe(true);
    expect(tabCount(oldVersion.layout)).toBe(0);

    const corrupt = parseLayout('{not json', PROJECT);
    expect(corrupt.migrated).toBe(true);
    expect(corrupt.layout).toEqual(defaultLayout(PROJECT));

    const missing = parseLayout(null, PROJECT);
    expect(missing.migrated).toBe(false);
  });

  it('sanitizes persisted panes: foreign tabs dropped and the limit enforced', () => {
    const payload = {
      version: 1,
      projectId: PROJECT,
      docked: true,
      focus: 'secondary',
      panes: {
        primary: {
          tabs: [
            ...Array.from({ length: 10 }, (_, index) => tab(`p${index}`)),
            tab('foreign', { projectId: 'project-2' }),
          ],
          activeTabId: 'foreign',
        },
        secondary: { tabs: [tab('s0')], activeTabId: 's0' },
      },
    };

    const restored = parseLayout(JSON.stringify(payload), PROJECT);

    expect(restored.migrated).toBe(false);
    expect(restored.layout.panes.primary.tabs).toHaveLength(MAX_TABS_PER_PANE);
    expect(restored.layout.panes.primary.tabs.every((item) => item.projectId === PROJECT)).toBe(true);
    // The active id pointed at a dropped tab, so it falls back to the first tab.
    expect(restored.layout.panes.primary.activeTabId).toBe('p0');
    expect(restored.layout.docked).toBe(true);
    expect(restored.layout.focus).toBe('secondary');
  });
});
