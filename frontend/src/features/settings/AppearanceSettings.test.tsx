import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { AppearanceSettings } from './AppearanceSettings';

const STORAGE_KEY = 'studio.ui-preferences';

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

describe('AppearanceSettings (U10)', () => {
  it('saves only presentation values and shows what is stored', () => {
    render(<AppearanceSettings />);

    fireEvent.change(screen.getByLabelText('Theme'), { target: { value: 'dark' } });
    fireEvent.change(screen.getByLabelText('Mật độ hiển thị'), { target: { value: 'compact' } });
    fireEvent.change(screen.getByLabelText('Cỡ chữ'), { target: { value: 'large' } });
    fireEvent.click(screen.getByLabelText('Giảm chuyển động'));
    fireEvent.click(screen.getByRole('button', { name: 'Lưu tùy chọn hiển thị' }));

    expect(screen.getByText(/Đã lưu tùy chọn hiển thị/)).toBeVisible();
    expect(screen.getByLabelText('Giá trị đang lưu')).toHaveTextContent('"theme":"dark"');
  });

  it('restores stored preferences and reports rejected keys without echoing them', () => {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ version: 1, theme: 'light', apiKey: 'sk-live-secret', styleGuide: 'Giọng cổ trang' }),
    );

    render(<AppearanceSettings />);

    expect(screen.getByLabelText('Theme')).toHaveValue('light');
    expect(screen.getByText(/Đã bỏ các khóa không hợp lệ/)).toBeVisible();
    expect(screen.getByLabelText('Giá trị đang lưu')).toHaveTextContent('"theme":"light"');
    expect(document.body.textContent).not.toContain('sk-live-secret');
    expect(document.body.textContent).not.toContain('Giọng cổ trang');
  });

  it('never writes a document that failed the safety check', () => {
    window.localStorage.setItem(STORAGE_KEY, '{not json');
    render(<AppearanceSettings />);

    fireEvent.click(screen.getByRole('button', { name: 'Lưu tùy chọn hiển thị' }));

    // Saving rewrites the corrupt document with a valid, minimal one.
    expect(JSON.parse(String(window.localStorage.getItem(STORAGE_KEY)))).toEqual({
      version: 1,
      theme: 'system',
      density: 'comfortable',
      fontScale: 'medium',
      reduceMotion: false,
    });
    expect(screen.getByText(/Đã lưu tùy chọn hiển thị/)).toBeVisible();
  });

  it('resets to defaults and clears the stored document', async () => {
    render(<AppearanceSettings />);
    fireEvent.change(screen.getByLabelText('Theme'), { target: { value: 'dark' } });
    fireEvent.click(screen.getByRole('button', { name: 'Lưu tùy chọn hiển thị' }));
    expect(window.localStorage.getItem(STORAGE_KEY)).not.toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Về mặc định' }));

    await waitFor(() => expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull());
    expect(screen.getByLabelText('Theme')).toHaveValue('system');
    expect(screen.getByText(/Đã đưa tùy chọn hiển thị về mặc định/)).toBeVisible();
  });
});
