import type { WorkspaceTabDefinition } from './WorkspaceTabs';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

type RouteDescriptor = {
  kind: WorkspaceTabDefinition['kind'];
  title: string;
};

const EXACT_ROUTES: Record<string, RouteDescriptor> = {
  '/': { kind: 'INSPECTOR', title: 'Thư viện' },
  '/jobs': { kind: 'JOB', title: 'Công việc' },
  '/diagnostics': { kind: 'QA', title: 'Chẩn đoán' },
  '/projects/new': { kind: 'INSPECTOR', title: 'Dự án mới' },
  '/settings': { kind: 'INSPECTOR', title: 'Cài đặt' },
};

const CHAPTER_ROUTES: Record<string, RouteDescriptor> = {
  translation: { kind: 'EDITOR', title: 'Dịch & hiệu đính' },
  voice: { kind: 'PREVIEW', title: 'Giọng đọc' },
  audio: { kind: 'PREVIEW', title: 'Audio' },
  export: { kind: 'INSPECTOR', title: 'Xuất bản' },
};

const PROJECT_ROUTES: Record<string, RouteDescriptor> = {
  import: { kind: 'INSPECTOR', title: 'Nhập nội dung' },
  batch: { kind: 'JOB', title: 'Hàng đợi batch' },
};

/**
 * Describe a route as a workspace tab (U05 round 3).
 *
 * The tab id is the pathname, so re-visiting a route focuses the existing tab
 * instead of opening a duplicate — the same rule the reducer enforces.
 */
export function describeRoute(pathname: string): WorkspaceTabDefinition {
  const clean = pathname === '' ? '/' : pathname;
  const exact = EXACT_ROUTES[clean];
  if (exact) {
    return { id: clean, kind: exact.kind, title: exact.title, chapterId: null, to: clean };
  }

  const chapter = /^\/chapters\/([^/]+)\/([^/]+)$/.exec(clean);
  if (chapter) {
    const descriptor = CHAPTER_ROUTES[chapter[2]] ?? { kind: 'EDITOR' as const, title: 'Chương' };
    return { id: clean, kind: descriptor.kind, title: descriptor.title, chapterId: chapter[1], to: clean };
  }

  const jobDraft = /^\/jobs\/([^/]+)\/([^/]+)$/.exec(clean);
  if (jobDraft) {
    return { id: clean, kind: 'JOB', title: 'Nháp job', chapterId: null, to: clean };
  }

  const project = /^\/projects\/([^/]+)\/([^/]+)(\/.*)?$/.exec(clean);
  if (project) {
    const descriptor =
      project[2] === 'settings'
        ? { kind: 'INSPECTOR' as const, title: 'Cài đặt dự án' }
        : (PROJECT_ROUTES[project[2]] ?? { kind: 'INSPECTOR' as const, title: 'Dự án' });
    return { id: clean, kind: descriptor.kind, title: descriptor.title, chapterId: null, to: clean };
  }

  if (clean.startsWith('/settings/')) {
    return { id: clean, kind: 'INSPECTOR', title: 'Cài đặt', chapterId: null, to: clean };
  }

  return { id: clean, kind: 'INSPECTOR', title: clean, chapterId: null, to: clean };
}

/**
 * Project that owns the persisted workspace layout for a route.
 *
 * Only routes carrying a project UUID resolve to a project: a chapter route
 * cannot be mapped to its project on the client yet, so it uses the local
 * (non-persisted) workspace bucket instead of writing into a wrong project.
 */
export function projectIdForRoute(pathname: string): string | null {
  const match = /^\/projects\/([^/]+)(\/|$)/.exec(pathname);
  if (!match || match[1] === 'new') {
    return null;
  }
  return UUID.test(match[1]) ? match[1] : null;
}
