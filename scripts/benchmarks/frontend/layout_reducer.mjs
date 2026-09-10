/**
 * V02 — driver Node đo CHÍNH reducer workspace của frontend (U05).
 *
 * Không sao chép logic: file này import `frontend/src/features/workspace/workspaceLayout.ts`
 * (module thật, chạy bằng type-stripping của Node 24) và in ra JSON để fixture
 * CHEDITOR8TAB đọc lại. Phần "long task >50 ms khi gõ" KHÔNG đo được ở đây vì cần
 * browser thật (PerformanceObserver/trace) — harness ghi NOT_RUN cho phần đó.
 */

import { register } from 'node:module';

register('./ts-resolve-hooks.mjs', import.meta.url);

const layoutModule = await import('../../../frontend/src/features/workspace/workspaceLayout.ts');
const {
  applyLayoutAction,
  defaultLayout,
  findTab,
  parseLayout,
  serializeLayout,
  tabCount,
  MAX_TABS_PER_PANE,
} = layoutModule;

const PROJECT = 'bench-project';
const OPEN_COUNT = MAX_TABS_PER_PANE + 1;

function tab(index, extra = {}) {
  return {
    id: `tab-${index}`,
    kind: 'EDITOR',
    projectId: PROJECT,
    chapterId: `chapter-${index}`,
    title: `Chương ${index}`,
    ...extra,
  };
}

function open(layout, index, extra) {
  return applyLayoutAction(layout, { type: 'open', projectId: PROJECT, tab: tab(index, extra) });
}

const result = {
  maxTabsPerPane: MAX_TABS_PER_PANE,
  nodeVersion: process.version,
};

// 1) Mở 9 tab: reducer phải giữ ≤8 tab/pane và báo tab bị evict (tab sạch).
let layout = defaultLayout(PROJECT);
let lastEvicted = null;
const openDurations = [];
for (let index = 0; index < OPEN_COUNT; index += 1) {
  const started = process.hrtime.bigint();
  const opened = open(layout, index);
  openDurations.push(Number(process.hrtime.bigint() - started) / 1e6);
  layout = opened.layout;
  lastEvicted = opened.evictedTabId ?? null;
}
result.tabsAfterNinthOpen = tabCount(layout);
result.primaryTabsAfterNinthOpen = layout.panes.primary.tabs.length;
result.evictedTabId = lastEvicted;
result.openAfterLimitStayedBounded = layout.panes.primary.tabs.length <= MAX_TABS_PER_PANE;
result.openP95Ms = percentile(openDurations, 0.95);

// 2) Tab bẩn KHÔNG được đóng ngầm: lần mở thứ 10 phải trả TAB_LIMIT_DIRTY và giữ nguyên layout.
const activeId = layout.panes.primary.activeTabId;
const dirty = applyLayoutAction(layout, { type: 'markDirty', projectId: PROJECT, tabId: activeId });
const dirtyLayout = dirty.layout;
const blocked = open(dirtyLayout, 99);
result.dirtyOpenError = blocked.error ?? null;
result.dirtyOpenKeptLayout = tabCount(blocked.layout) === tabCount(dirtyLayout);
result.dirtyOpenEvictedNothing = blocked.evictedTabId === undefined || blocked.evictedTabId === null;

// 3) Evict (đóng tab sạch) rồi reopen: tab quay lại, không nhân bản, draft id giữ nguyên.
const victim = dirtyLayout.panes.primary.tabs[1].id;
const closed = applyLayoutAction(dirtyLayout, { type: 'close', projectId: PROJECT, tabId: victim });
const reopened = applyLayoutAction(closed.layout, { type: 'open', projectId: PROJECT, tab: tab(1) });
const reopenedIds = reopened.layout.panes.primary.tabs.map((item) => item.id);
result.reopenCountForVictim = reopenedIds.filter((id) => id === victim).length;
result.reopenRestoredCount = tabCount(reopened.layout);
result.reopenActiveTabId = reopened.layout.panes.primary.activeTabId;
result.reopenFindsTab = findTab(reopened.layout, victim) !== null;

// 4) Persist/restore layout: 8 tab sống qua serialize → parse (reopen sau khi reload).
const roundTrip = parseLayout(serializeLayout(reopened.layout), PROJECT);
result.roundTripTabCount = tabCount(roundTrip.layout);
result.roundTripMigrated = roundTrip.migrated;
result.roundTripIdsMatch =
  JSON.stringify(roundTrip.layout.panes.primary.tabs.map((item) => item.id)) ===
  JSON.stringify(reopened.layout.panes.primary.tabs.map((item) => item.id));

// 5) Chi phí reducer thuần (không phải long task của browser): 5.000 thao tác activate/markDirty.
const opDurations = [];
let work = reopened.layout;
for (let index = 0; index < 5_000; index += 1) {
  const targetId = work.panes.primary.tabs[index % work.panes.primary.tabs.length].id;
  const action =
    index % 2 === 0
      ? { type: 'markDirty', projectId: PROJECT, tabId: targetId }
      : { type: 'activate', projectId: PROJECT, pane: 'primary', tabId: targetId };
  const started = process.hrtime.bigint();
  work = applyLayoutAction(work, action).layout;
  opDurations.push(Number(process.hrtime.bigint() - started) / 1e6);
}
result.reducerOps = opDurations.length;
result.reducerOpP50Ms = percentile(opDurations, 0.5);
result.reducerOpP95Ms = percentile(opDurations, 0.95);
result.reducerOpMaxMs = Math.max(...opDurations);

function percentile(values, quantile) {
  const ordered = [...values].sort((left, right) => left - right);
  if (ordered.length === 0) {
    return 0;
  }
  const position = (ordered.length - 1) * quantile;
  const lower = Math.floor(position);
  const upper = Math.ceil(position);
  if (lower === upper) {
    return Number(ordered[lower].toFixed(6));
  }
  const weight = position - lower;
  return Number((ordered[lower] + (ordered[upper] - ordered[lower]) * weight).toFixed(6));
}

console.log(JSON.stringify(result));
