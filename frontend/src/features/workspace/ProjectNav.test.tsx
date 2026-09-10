import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { useEffect } from 'react';
import {
  MemoryRouter,
  Outlet,
  Route,
  Routes,
  useLocation,
  useNavigate,
  type NavigateFunction,
} from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { projectSettingsRoutes } from '../settings/ProjectSettingsRoutes';
import { ProjectNav } from './ProjectNav';

function jsonResponse(body: unknown, status = 200): Response {
  const text = JSON.stringify(body);
  return { ok: status < 400, status, text: async () => text, json: async () => body } as Response;
}

/** Storage/voice endpoints the nested panels call; anything else fails loudly. */
function mockPanelFetch() {
  const calls: string[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      calls.push(url);
      if (url.endsWith('/api/security/bootstrap')) {
        return jsonResponse({ csrfToken: 'test-token' });
      }
      if (url === '/api/storage/disk') {
        return jsonResponse({
          allowed: true,
          level: 'OK',
          usedBytes: 1024,
          freeBytes: 2048,
          estimatedBytes: 0,
          reasons: [],
        });
      }
      if (url === '/api/storage/backups') {
        return jsonResponse({ backups: [] });
      }
      if (url === '/api/voices?locale=vi-VN') {
        return jsonResponse({ previewText: 'Xin chào', voices: [] });
      }
      if (url.startsWith('/api/projects/p1/styles') || url.startsWith('/api/projects/p1/glossary')) {
        return jsonResponse({});
      }
      throw new Error(`unexpected url ${url}`);
    }),
  );
  return calls;
}

/**
 * `navigate` is captured so a test can exercise the router's own history
 * (Back) rather than only asserting on rendered links.
 */
const navigateRef: { current: NavigateFunction | null } = { current: null };

function RouterProbe() {
  const navigate = useNavigate();
  const location = useLocation();
  useEffect(() => {
    navigateRef.current = navigate;
  }, [navigate]);
  return (
    <p role="status" aria-label="route">
      {location.pathname}
    </p>
  );
}

function Shell() {
  return (
    <>
      <ProjectNav projectId="p1" />
      <RouterProbe />
      <Outlet />
    </>
  );
}

function renderShell(entries: string[], initialIndex = entries.length - 1) {
  return render(
    <MemoryRouter initialEntries={entries} initialIndex={initialIndex}>
      <Routes>
        <Route path="/" element={<Shell />}>
          <Route path="projects/:projectId" element={<p>Nhập nội dung dự án</p>} />
          {projectSettingsRoutes.map((route) => (
            <Route key={route.path} path={route.path} element={route.element}>
              {(route.children ?? []).map((child) => (
                <Route
                  key={child.path ?? '(index)'}
                  index={child.index}
                  path={child.path}
                  element={child.element}
                />
              ))}
            </Route>
          ))}
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

function currentPath(): string {
  return screen.getByLabelText('route').textContent ?? '';
}

/** Wait until the settings child panel has replaced the shell content. */
async function waitForPath(expected: string) {
  await waitFor(() => expect(currentPath()).toBe(expected));
}

beforeEach(() => {
  navigateRef.current = null;
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('ProjectNav (U02 route context)', () => {
  it('keeps the project in the breadcrumb and marks the active area', () => {
    render(
      <MemoryRouter initialEntries={['/projects/p1/batch']}>
        <Routes>
          <Route path="/projects/:projectId/*" element={<ProjectNav projectId="p1" projectTitle="Truyện A" />} />
        </Routes>
      </MemoryRouter>,
    );

    const nav = screen.getByRole('navigation', { name: 'Dự án' });
    expect(within(nav).getByLabelText('Đường dẫn')).toHaveTextContent('Thư viện / Truyện A');
    expect(within(nav).getByRole('link', { name: 'Hàng đợi batch' })).toHaveAttribute('aria-current', 'page');
    expect(within(nav).getByRole('link', { name: 'Nhập nội dung' })).not.toHaveAttribute('aria-current');
    // The project context is visible on every project route.
    expect(within(nav).getByLabelText('Ngữ cảnh dự án')).toHaveTextContent('p1');
  });

  it('exposes the project areas as focusable links in order, and activating one follows it', async () => {
    mockPanelFetch();
    renderShell(['/projects/p1']);

    const nav = screen.getByRole('navigation', { name: 'Dự án' });
    const links = within(nav).getAllByRole('link');
    // Every area is a real link, so it is keyboard reachable in DOM order.
    expect(links.map((link) => link.textContent)).toEqual([
      'Thư viện',
      'Nhập nội dung',
      'Hàng đợi batch',
      'Cài đặt dự án',
    ]);
    for (const link of links) {
      link.focus();
      expect(link).toHaveFocus();
    }

    // After entering the project we are still on a project route.
    expect(currentPath()).toBe('/projects/p1');

    // Activating the last area (Enter on a focused link, here its click handler)
    // goes into the nested settings tree and lands on its index group.
    const settingsLink = within(nav).getByRole('link', { name: 'Cài đặt dự án' });
    settingsLink.focus();
    fireEvent.click(settingsLink);
    await waitForPath('/projects/p1/settings/translation');

    // The project context survives the navigation.
    expect(screen.getByRole('navigation', { name: 'Dự án' })).toHaveTextContent('p1');
  });
});

describe('project settings route integration (U02)', () => {
  it('deep-links into a nested group and shows real content, not a placeholder', async () => {
    const calls = mockPanelFetch();
    renderShell(['/projects/p1/settings/storage']);

    expect(await screen.findByLabelText('Storage')).toBeVisible();
    expect(calls).toContain('/api/storage/disk');
    // The old placeholder copy is gone from every nested group.
    expect(screen.queryByText(/sẽ nối ở U10/)).not.toBeInTheDocument();
    expect(screen.queryByText(/sẽ nối ở A02/)).not.toBeInTheDocument();
  });

  it('keeps the project context when the user moves back to another group', async () => {
    mockPanelFetch();
    renderShell(['/projects/p1/settings/storage', '/projects/p1/settings/tts']);

    expect(await screen.findByLabelText('TTS dự án')).toBeVisible();
    expect(currentPath()).toBe('/projects/p1/settings/tts');
    expect(screen.getByRole('navigation', { name: 'Dự án' })).toHaveTextContent('p1');

    // The group sidebar switches between nested groups with real links.
    const groupNav = screen.getByRole('navigation', { name: 'Nhóm cài đặt dự án' });
    fireEvent.click(within(groupNav).getByRole('link', { name: 'Storage' }));
    expect(await screen.findByLabelText('Storage')).toBeVisible();
    await waitForPath('/projects/p1/settings/storage');
    expect(screen.getByRole('navigation', { name: 'Dự án' })).toHaveTextContent('p1');
  });

  it('keeps the project context through the router history Back action', async () => {
    mockPanelFetch();
    renderShell(['/projects/p1/settings/storage', '/projects/p1/settings/tts']);

    expect(await screen.findByLabelText('TTS dự án')).toBeVisible();
    await waitForPath('/projects/p1/settings/tts');

    await act(async () => {
      await navigateRef.current?.(-1);
    });

    await waitForPath('/projects/p1/settings/storage');
    expect(await screen.findByLabelText('Storage')).toBeVisible();
    // Back returns inside the same project, so the breadcrumb context is intact.
    expect(screen.getByRole('navigation', { name: 'Dự án' })).toHaveTextContent('p1');
  });
});
