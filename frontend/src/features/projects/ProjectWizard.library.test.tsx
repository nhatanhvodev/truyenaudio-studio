import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ProjectWizard } from './ProjectWizard';

type Call = { url: string; init?: RequestInit };

function project(index: number) {
  return {
    id: `project-${index}`,
    title: `Truyện ${index}`,
    slug: `truyen-${index}`,
    sourceType: 'SELF_AUTHORED',
    rightsStatus: 'PRIVATE_ONLY',
    createdAt: '2026-09-01T00:00:00+00:00',
    updatedAt: '2026-09-01T00:00:00+00:00',
    chapterCount: 120,
    firstChapterId: `chapter-${index}-1`,
    chapters: Array.from({ length: 30 }, (_, position) => ({
      id: `chapter-${index}-${position + 1}`,
      ordinal: position + 1,
      title: `Chương ${position + 1}`,
      state: 'IMPORTED',
    })),
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  const text = JSON.stringify(body);
  return { ok: status < 400, status, text: async () => text, json: async () => body } as Response;
}

function mockFetch(handler: (url: string, init?: RequestInit) => Response) {
  const calls: Call[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith('/api/security/bootstrap')) {
        return jsonResponse({ csrfToken: 'test-token' });
      }
      calls.push({ url, init });
      return handler(url, init);
    }),
  );
  return calls;
}

function renderLibrary() {
  return render(
    <MemoryRouter>
      <ProjectWizard />
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('ProjectWizard library paging (U03)', () => {
  it('requests a bounded first page with the page metadata and shows the total', async () => {
    const calls = mockFetch((url) => {
      if (url.startsWith('/api/projects?')) {
        return jsonResponse({
          projects: [project(1), project(2)],
          page: { limit: 20, total: 40, count: 2, hasMore: true, nextCursor: 'cursor-2', mountedChaptersPerProject: 30 },
        });
      }
      throw new Error(`unexpected url ${url}`);
    });

    renderLibrary();

    expect(await screen.findByText(/Dự án hiện có \(2\/40\)/)).toBeVisible();
    const first = calls.find((call) => call.url.startsWith('/api/projects?'));
    expect(first?.url).toContain('limit=20');
    expect(new URLSearchParams(first?.url.split('?')[1]).get('cursor')).toBeNull();
    // The list only mounts the server-provided summaries (30 per project).
    expect(screen.getAllByRole('article').length).toBeGreaterThanOrEqual(2);
  });

  it('appends the next page when the user asks for more', async () => {
    const calls = mockFetch((url) => {
      if (url.includes('cursor=cursor-2')) {
        return jsonResponse({
          projects: [project(3)],
          page: { limit: 20, total: 3, count: 1, hasMore: false, nextCursor: null, mountedChaptersPerProject: 30 },
        });
      }
      return jsonResponse({
        projects: [project(1), project(2)],
        page: { limit: 20, total: 3, count: 2, hasMore: true, nextCursor: 'cursor-2', mountedChaptersPerProject: 30 },
      });
    });

    renderLibrary();
    const more = await screen.findByRole('button', { name: 'Tải thêm dự án' });

    fireEvent.click(more);

    await waitFor(() => expect(screen.getByText(/Dự án hiện có \(3\/3\)/)).toBeVisible());
    expect(screen.queryByRole('button', { name: 'Tải thêm dự án' })).not.toBeInTheDocument();
    // The appended page did not re-fetch the first one.
    expect(calls.filter((call) => call.url.includes('cursor=cursor-2'))).toHaveLength(1);
  });

  it('filters server-side and tolerates a backend without page metadata', async () => {
    const calls = mockFetch((url) => {
      if (url.includes('q=')) {
        return jsonResponse({ projects: [project(9)] });
      }
      return jsonResponse({ projects: [project(1)] });
    });

    renderLibrary();
    await screen.findByText(/Dự án hiện có \(1\)/);

    fireEvent.change(screen.getByLabelText('Tìm dự án'), { target: { value: 'Truyện 9' } });
    fireEvent.click(screen.getByRole('button', { name: 'Tìm / Làm mới' }));

    await waitFor(() => expect(screen.getByText(/Dự án hiện có \(1\)/)).toBeVisible());
    const searchCall = calls.find((call) => call.url.includes('q='));
    const params = new URLSearchParams(searchCall?.url.split('?')[1] ?? '');
    expect(params.get('q')).toBe('Truyện 9');
    // No page metadata means no "load more" affordance rather than a broken list.
    expect(screen.queryByRole('button', { name: 'Tải thêm dự án' })).not.toBeInTheDocument();
  });
});
