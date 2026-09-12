import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { AppearanceSettings } from './AppearanceSettings';
import { ThemeProvider } from './ThemeProvider';

const STORAGE_KEY = 'studio.ui-preferences';

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  document.documentElement.removeAttribute('data-reduce-motion');
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
    const notice = screen.getByText(/Đã bỏ các khóa không hợp lệ/);
    expect(notice).toBeVisible();
    // The count is the whole message; it is what the user gets instead of a list.
    expect(notice).toHaveTextContent('2 khóa');
    expect(screen.getByLabelText('Giá trị đang lưu')).toHaveTextContent('"theme":"light"');

    // The assertions BELOW are the ones this test was missing. Its title has
    // always claimed the keys are reported "without echoing them", but only the
    // VALUES were ever checked - so the screen could list `apiKey` and
    // `styleGuide` by name, and did, while this test stayed green. The names come
    // from the stored document, so anyone who can write this origin's
    // localStorage chooses them; they belong in `rejectedKeys` for the parser's
    // callers, not in the DOM.
    expect(document.body.textContent).not.toContain('apiKey');
    expect(document.body.textContent).not.toContain('styleGuide');
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
      theme: 'dark',
      density: 'comfortable',
      fontScale: 'medium',
      reduceMotion: false,
    });
    expect(screen.getByText(/Đã lưu tùy chọn hiển thị/)).toBeVisible();
  });

  it('resets to defaults and clears the stored document', async () => {
    render(<AppearanceSettings />);
    fireEvent.change(screen.getByLabelText('Theme'), { target: { value: 'light' } });
    fireEvent.click(screen.getByRole('button', { name: 'Lưu tùy chọn hiển thị' }));
    expect(window.localStorage.getItem(STORAGE_KEY)).not.toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Về mặc định' }));

    await waitFor(() => expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull());
    expect(screen.getByLabelText('Theme')).toHaveValue('dark');
    expect(screen.getByText(/Đã đưa tùy chọn hiển thị về mặc định/)).toBeVisible();
  });

  it('applies the reduced-motion preference to the document', () => {
    render(
      <ThemeProvider>
        <AppearanceSettings />
      </ThemeProvider>,
    );

    // The provider applied the stored defaults on mount.
    expect(document.documentElement.dataset.reduceMotion).toBe('false');

    fireEvent.click(screen.getByLabelText('Giảm chuyển động'));
    fireEvent.click(screen.getByRole('button', { name: 'Lưu tùy chọn hiển thị' }));
    expect(document.documentElement.dataset.reduceMotion).toBe('true');
  });
});
