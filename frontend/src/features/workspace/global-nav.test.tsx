import { cleanup, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it } from 'vitest';

import { GlobalNav } from './GlobalNav';

afterEach(cleanup);

function renderAt(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <GlobalNav />
    </MemoryRouter>,
  );
  return screen.getByRole('navigation', { name: 'Khu vực' });
}

describe('GlobalNav (U02 part 2)', () => {
  it('exposes the three global areas', () => {
    const nav = renderAt('/jobs');
    expect(within(nav).getByRole('link', { name: 'Thư viện' })).toBeTruthy();
    expect(within(nav).getByRole('link', { name: 'Công việc' })).toBeTruthy();
    expect(within(nav).getByRole('link', { name: 'Cài đặt' })).toBeTruthy();
  });

  it('marks the active area with aria-current', () => {
    const nav = renderAt('/settings/providers');
    expect(within(nav).getByRole('link', { name: 'Cài đặt' }).getAttribute('aria-current')).toBe('page');
    expect(within(nav).getByRole('link', { name: 'Thư viện' }).getAttribute('aria-current')).toBeNull();
  });

  it('does not mark the library as active on other routes', () => {
    const nav = renderAt('/jobs');
    expect(within(nav).getByRole('link', { name: 'Thư viện' }).getAttribute('aria-current')).toBeNull();
    expect(within(nav).getByRole('link', { name: 'Công việc' }).getAttribute('aria-current')).toBe('page');
  });
});
