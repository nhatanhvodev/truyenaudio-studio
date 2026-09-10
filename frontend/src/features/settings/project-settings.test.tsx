import { cleanup, render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it } from 'vitest';

import { PROJECT_SETTINGS_GROUPS, ProjectSettingsShell, projectSettingsRoutes } from './ProjectSettingsRoutes';

afterEach(cleanup);

function renderShell(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/projects/:projectId/settings" element={<ProjectSettingsShell />}>
          <Route index element={<div>index-stub</div>} />
          <Route path="translation" element={<div>translation-stub</div>} />
          <Route path="storage" element={<div>storage-stub</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe('Project settings routes (U02 part 3)', () => {
  it('links every group under the project path and marks the active one', () => {
    renderShell('/projects/p-1/settings/translation');
    const nav = screen.getByRole('navigation', { name: 'Nhóm cài đặt dự án' });
    for (const group of PROJECT_SETTINGS_GROUPS) {
      const link = within(nav).getByRole('link', { name: group.label });
      expect(link.getAttribute('href')).toBe(`/projects/p-1/settings/${group.slug}`);
    }
    expect(
      within(nav).getByRole('link', { name: 'Translation' }).getAttribute('aria-current'),
    ).toBe('page');
    expect(screen.getByText('p-1')).toBeTruthy();
    expect(screen.getByText('translation-stub')).toBeTruthy();
  });

  it('renders only the active group outlet and a link back to the project', () => {
    renderShell('/projects/p-2/settings/storage');
    expect(screen.getByText('storage-stub')).toBeTruthy();
    expect(screen.queryByText('translation-stub')).toBeNull();
    expect(screen.getByRole('link', { name: /Về import của dự án/ }).getAttribute('href')).toBe(
      '/projects/p-2/import',
    );
  });

  it('declares the project settings tree with five groups plus the index redirect', () => {
    const parents = projectSettingsRoutes.filter((route) => route.path === 'projects/:projectId/settings');
    expect(parents).toHaveLength(1);
    const paths = (parents[0].children ?? []).map((child) => child.path ?? '(index)');
    expect(paths).toEqual(['(index)', 'translation', 'tts', 'storage', 'appearance', 'advanced']);
  });
});
