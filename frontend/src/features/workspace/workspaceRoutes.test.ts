import { describe, expect, it } from 'vitest';

import { describeRoute, projectIdForRoute } from './workspaceRoutes';

const PROJECT_UUID = '018f0000-0000-7000-8000-000000000901';

describe('workspace route description (U05 round 3)', () => {
  it('maps the main areas to tabs', () => {
    expect(describeRoute('/')).toMatchObject({ id: '/', kind: 'INSPECTOR', title: 'Thư viện' });
    expect(describeRoute('/jobs')).toMatchObject({ kind: 'JOB', title: 'Công việc' });
    expect(describeRoute('/diagnostics')).toMatchObject({ kind: 'QA', title: 'Chẩn đoán' });
    expect(describeRoute('/settings/providers')).toMatchObject({ kind: 'INSPECTOR', title: 'Cài đặt' });
  });

  it('maps chapter routes with the chapter id and the right kind', () => {
    expect(describeRoute('/chapters/c1/translation')).toMatchObject({
      id: '/chapters/c1/translation',
      kind: 'EDITOR',
      title: 'Dịch & hiệu đính',
      chapterId: 'c1',
    });
    expect(describeRoute('/chapters/c1/voice')).toMatchObject({ kind: 'PREVIEW', chapterId: 'c1' });
    expect(describeRoute('/chapters/c1/audio')).toMatchObject({ kind: 'PREVIEW', chapterId: 'c1' });
    expect(describeRoute('/chapters/c1/export')).toMatchObject({ kind: 'INSPECTOR', chapterId: 'c1' });
  });

  it('maps project routes and falls back for unknown paths', () => {
    expect(describeRoute(`/projects/${PROJECT_UUID}/import`)).toMatchObject({ kind: 'INSPECTOR', title: 'Nhập nội dung' });
    expect(describeRoute(`/projects/${PROJECT_UUID}/batch`)).toMatchObject({ kind: 'JOB', title: 'Hàng đợi batch' });
    expect(describeRoute(`/projects/${PROJECT_UUID}/settings/translation`)).toMatchObject({ title: 'Cài đặt dự án' });
    expect(describeRoute('/something/else')).toMatchObject({ id: '/something/else', title: '/something/else' });
  });

  it('only resolves a persisted layout project for project-UUID routes', () => {
    expect(projectIdForRoute(`/projects/${PROJECT_UUID}/import`)).toBe(PROJECT_UUID);
    expect(projectIdForRoute(`/projects/${PROJECT_UUID}`)).toBe(PROJECT_UUID);
    expect(projectIdForRoute('/projects/new')).toBeNull();
    expect(projectIdForRoute('/projects/project-1/import')).toBeNull();
    expect(projectIdForRoute('/chapters/c1/translation')).toBeNull();
    expect(projectIdForRoute('/jobs')).toBeNull();
  });
});
