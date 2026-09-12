import { cleanup, render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { ThemeChangeNotice } from './ThemeChangeNotice';
import {
  THEME_NOTICE_STORAGE_KEY,
  UI_PREFERENCES_STORAGE_KEY,
  acknowledgeDarkDefaultNotice,
  shouldAnnounceDarkDefault,
} from './themeRuntime';

const originalMatchMedia = window.matchMedia;

function stubSystemTheme(dark: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    configurable: true,
    value: (query: string) => ({
      matches: query.includes('prefers-color-scheme: dark') ? dark : false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}

function renderNotice() {
  return render(
    <MemoryRouter>
      <ThemeChangeNotice />
    </MemoryRouter>,
  );
}

describe('shouldAnnounceDarkDefault', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    // vitest runs with `globals: false`, so @testing-library/react never
    // registers its automatic cleanup - every suite in this repo unmounts by
    // hand. Without this a second render in the same file sees the first one's
    // DOM and queries start matching twice.
    cleanup();
    window.localStorage.clear();
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      configurable: true,
      value: originalMatchMedia,
    });
  });

  it('announces for the only cohort the dark default actually changes', () => {
    stubSystemTheme(false);
    expect(shouldAnnounceDarkDefault()).toBe(true);
  });

  it('stays silent when the operating system is already dark', () => {
    // Nothing changed for this user: the old default resolved to dark on a dark
    // OS too. Telling them "the theme changed" would be false.
    stubSystemTheme(true);
    expect(shouldAnnounceDarkDefault()).toBe(false);
  });

  it('stays silent when any preference document is stored, including "system"', () => {
    stubSystemTheme(false);
    // An explicit "system" is the interesting case: the value itself did not
    // change meaning, and the user has made a choice, so they are not surprised.
    for (const theme of ['light', 'dark', 'system']) {
      window.localStorage.setItem(UI_PREFERENCES_STORAGE_KEY, JSON.stringify({ version: 1, theme }));
      expect(shouldAnnounceDarkDefault(), `stored theme=${theme}`).toBe(false);
      window.localStorage.clear();
    }
  });

  it('stays silent once acknowledged', () => {
    stubSystemTheme(false);
    acknowledgeDarkDefaultNotice();
    expect(shouldAnnounceDarkDefault()).toBe(false);
  });

  it('stays silent when storage is blocked, rather than showing an undismissable notice', () => {
    stubSystemTheme(false);
    // Redefine the ACCESSOR, not `getItem`: jsdom's Storage is a proxy, so a spy
    // on the method is not what the component's `window.localStorage.getItem`
    // call goes through. A blocked store makes the property access itself throw,
    // which is also how a browser with cookies disabled behaves.
    const original = Object.getOwnPropertyDescriptor(window, 'localStorage');
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      get() {
        throw new Error('storage blocked');
      },
    });
    try {
      expect(shouldAnnounceDarkDefault()).toBe(false);
    } finally {
      if (original) Object.defineProperty(window, 'localStorage', original);
    }
  });
});

describe('ThemeChangeNotice', () => {
  beforeEach(() => {
    window.localStorage.clear();
    stubSystemTheme(false);
  });

  afterEach(() => {
    // vitest runs with `globals: false`, so @testing-library/react never
    // registers its automatic cleanup - every suite in this repo unmounts by
    // hand. Without this a second render in the same file sees the first one's
    // DOM and queries start matching twice.
    cleanup();
    window.localStorage.clear();
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      configurable: true,
      value: originalMatchMedia,
    });
  });

  it('renders for the affected cohort and links to the setting that changes it', () => {
    renderNotice();

    expect(screen.getByText(/Giao diện mặc định đã chuyển sang bản tối/)).toBeVisible();
    expect(screen.getByRole('link', { name: 'Cài đặt › Appearance' })).toHaveAttribute(
      'href',
      '/settings/appearance',
    );
  });

  it('renders nothing for a user the change does not affect', () => {
    stubSystemTheme(true);
    renderNotice();
    expect(screen.queryByText(/Giao diện mặc định đã chuyển sang bản tối/)).toBeNull();
  });

  it('dismisses and stays dismissed across a remount', () => {
    const first = renderNotice();
    fireEvent.click(screen.getByRole('button', { name: 'Đóng thông báo giao diện' }));
    expect(screen.queryByText(/Giao diện mặc định đã chuyển sang bản tối/)).toBeNull();
    first.unmount();

    // The acknowledgement must be PERSISTED, not just local state - otherwise
    // the notice returns on every load and is worse than not having it.
    expect(window.localStorage.getItem(THEME_NOTICE_STORAGE_KEY)).not.toBeNull();
    renderNotice();
    expect(screen.queryByText(/Giao diện mặc định đã chuyển sang bản tối/)).toBeNull();
  });
});
