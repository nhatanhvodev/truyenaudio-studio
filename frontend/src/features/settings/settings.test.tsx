import { cleanup, render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it } from 'vitest';

import { SETTINGS_GROUPS, SettingsLayout } from './SettingsLayout';
import { settingsGroupRoutes } from './SettingsRoutes';

afterEach(cleanup);

function renderLayout(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/settings" element={<SettingsLayout />}>
          <Route index element={<div>index-stub</div>} />
          <Route path="storage" element={<div>storage-stub</div>} />
          <Route path="models" element={<div>models-stub</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe('Settings shell (U02 part 1)', () => {
  it('lists exactly the seven settings groups in the nav', () => {
    renderLayout('/settings/storage');
    const nav = screen.getByRole('navigation', { name: 'Nhóm cài đặt' });
    const links = within(nav).getAllByRole('link');
    expect(links).toHaveLength(7);
    for (const group of SETTINGS_GROUPS) {
      expect(within(nav).getByRole('link', { name: group.label })).toBeTruthy();
    }
  });

  it('marks the active group and renders the breadcrumb', () => {
    renderLayout('/settings/storage');
    const nav = screen.getByRole('navigation', { name: 'Nhóm cài đặt' });
    expect(within(nav).getByRole('link', { name: 'Storage' }).getAttribute('aria-current')).toBe('page');
    expect(within(nav).getByRole('link', { name: 'Models' }).getAttribute('aria-current')).toBeNull();
    expect(screen.getByLabelText('Đường dẫn').textContent).toContain('Cài đặt / Storage');
    expect(screen.getByText('storage-stub')).toBeTruthy();
  });

  it('renders only the active group outlet', () => {
    renderLayout('/settings/models');
    expect(screen.getByText('models-stub')).toBeTruthy();
    expect(screen.queryByText('storage-stub')).toBeNull();
  });

  it('declares the seven group routes plus the index redirect', () => {
    const parents = settingsGroupRoutes.filter((route) => route.path === 'settings');
    expect(parents).toHaveLength(1);
    const children = parents[0].children ?? [];
    const paths = children.map((child) => child.path ?? '(index)');
    expect(paths).toEqual([
      '(index)',
      'providers',
      'models',
      'translation',
      'tts',
      'storage',
      'appearance',
      'advanced',
    ]);
  });
});
