import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from './App';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, '', '/');
});

describe('App', () => {
  it('opens on the project and rights workflow instead of a landing page', async () => {
    render(<App />);

    expect(await screen.findByRole('heading', { name: 'Truyện Audio Studio' })).toBeVisible();
    expect(screen.getByLabelText('Tên truyện')).toBeVisible();
    expect(screen.getByLabelText('Loại nguồn')).toBeVisible();
    expect(screen.getByLabelText('Trạng thái quyền')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Tạo dự án' })).toBeVisible();
  });

  it('keeps the jobs overlay available without browser EventSource support', async () => {
    vi.stubGlobal('EventSource', undefined);

    render(<App />);

    expect(await screen.findByLabelText('Jobs overlay')).toBeVisible();
    expect(screen.getByText('Chưa có job đang chạy')).toBeVisible();
  });
});
