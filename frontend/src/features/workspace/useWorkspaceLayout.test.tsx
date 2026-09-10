import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { useWorkspaceLayout } from './useWorkspaceLayout';

const PROJECT = 'project-1';

function jsonResponse(body: unknown, status = 200): Response {
  const text = JSON.stringify(body);
  return {
    ok: status < 400,
    status,
    text: async () => text,
    json: async () => body,
  } as Response;
}

function mockFetch(handler: (url: string, init?: RequestInit) => Response) {
  const calls: { url: string; init?: RequestInit }[] = [];
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/api/security/bootstrap')) {
      return jsonResponse({ csrfToken: 'test-token' });
    }
    calls.push({ url, init });
    return handler(url, init);
  });
  vi.stubGlobal('fetch', fn);
  return calls;
}

function persisted(tabIds: string[], revision: number, extra: Record<string, unknown> = {}) {
  return {
    projectId: PROJECT,
    revision,
    migrated: false,
    layout: {
      version: 1,
      projectId: PROJECT,
      docked: false,
      focus: 'primary',
      panes: {
        primary: {
          tabs: tabIds.map((id) => ({
            id,
            kind: 'EDITOR',
            projectId: PROJECT,
            chapterId: `chapter-${id}`,
            title: `Tab ${id}`,
            dirty: false,
          })),
          activeTabId: tabIds[tabIds.length - 1] ?? null,
        },
        secondary: { tabs: [], activeTabId: null },
      },
      ...extra,
    },
  };
}

function Harness({ enabled = true }: { enabled?: boolean }) {
  const state = useWorkspaceLayout({ projectId: PROJECT, enabled });
  return (
    <div>
      <p role="status" aria-label="tabs">
        {state.layout.panes.primary.tabs.map((tab) => tab.id).join(',') ||
          (state.loading ? 'đang tải' : 'trống')}
      </p>
      <p role="status" aria-label="revision">
        {state.revision === null ? 'chưa có' : `bản ${state.revision}`}
      </p>
      <p role="status" aria-label="dirty">
        {state.dirty ? 'có thay đổi' : 'đã lưu'}
      </p>
      {state.migrated ? <p role="status">layout đã được chuyển đổi</p> : null}
      {state.error ? <p role="alert">{state.error}</p> : null}
      {state.conflict ? <p role="status">xung đột layout</p> : null}
      <button type="button" onClick={() => state.dispatch({ type: 'open', projectId: PROJECT, tab: { id: 'new', kind: 'QA', projectId: PROJECT, chapterId: null, title: 'QA' } })}>
        mở tab
      </button>
      <button
        type="button"
        onClick={() =>
          state.dispatch({ type: 'open', projectId: PROJECT, tab: { id: 'x', kind: 'QA', projectId: 'other', chapterId: null, title: 'X' } })
        }
      >
        mở tab project khác
      </button>
      <button type="button" onClick={() => void state.save()}>
        lưu layout
      </button>
      <button type="button" onClick={() => void state.reloadServer()}>
        tải lại
      </button>
    </div>
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('useWorkspaceLayout (U05 round 2)', () => {
  it('restores the persisted layout and revision for the project', async () => {
    const calls = mockFetch(() => jsonResponse(persisted(['t0', 't1'], 3)));

    render(<Harness />);

    await waitFor(() => expect(screen.getByLabelText('tabs')).toHaveTextContent('t0,t1'));
    expect(screen.getByLabelText('revision')).toHaveTextContent('bản 3');
    expect(screen.getByLabelText('dirty')).toHaveTextContent('đã lưu');
    expect(calls[0].url).toBe(`/api/projects/${PROJECT}/workspace-layout`);
  });

  it('falls back to the default layout when the server reports a migrated layout', async () => {
    mockFetch(() =>
      jsonResponse({ projectId: PROJECT, layout: null, revision: 7, migrated: true }),
    );

    render(<Harness />);

    await waitFor(() => expect(screen.getByText('layout đã được chuyển đổi')).toBeVisible());
    expect(screen.getByLabelText('tabs')).toHaveTextContent('trống');
    expect(screen.getByLabelText('revision')).toHaveTextContent('bản 7');
  });

  it('applies local actions, refuses cross-project tabs, and marks the layout dirty', async () => {
    mockFetch(() => jsonResponse(persisted(['t0'], 2)));

    render(<Harness />);
    await waitFor(() => expect(screen.getByLabelText('tabs')).toHaveTextContent('t0'));

    fireEvent.click(screen.getByRole('button', { name: 'mở tab project khác' }));
    expect(screen.getByRole('alert')).toHaveTextContent('PROJECT_MISMATCH');
    expect(screen.getByLabelText('tabs')).toHaveTextContent('t0');

    fireEvent.click(screen.getByRole('button', { name: 'mở tab' }));
    expect(screen.getByLabelText('tabs')).toHaveTextContent('t0,new');
    expect(screen.getByLabelText('dirty')).toHaveTextContent('có thay đổi');
  });

  it('saves with compare-and-swap and adopts the returned revision', async () => {
    const bodies: unknown[] = [];
    mockFetch((url, init) => {
      if ((init?.method ?? 'GET') === 'PUT') {
        bodies.push(JSON.parse(String(init?.body)));
        return jsonResponse(persisted(['t0', 'new'], 3));
      }
      return jsonResponse(persisted(['t0'], 2));
    });

    render(<Harness />);
    await waitFor(() => expect(screen.getByLabelText('tabs')).toHaveTextContent('t0'));
    fireEvent.click(screen.getByRole('button', { name: 'mở tab' }));
    fireEvent.click(screen.getByRole('button', { name: 'lưu layout' }));

    await waitFor(() => expect(screen.getByLabelText('revision')).toHaveTextContent('bản 3'));
    expect(bodies[0]).toMatchObject({ expected_revision: 2 });
    expect(JSON.stringify(bodies[0])).toContain('"new"');
    expect(screen.getByLabelText('dirty')).toHaveTextContent('đã lưu');
  });

  it('keeps the local layout on a revision conflict and can reload the server version', async () => {
    let putCount = 0;
    mockFetch((url, init) => {
      if ((init?.method ?? 'GET') === 'PUT') {
        putCount += 1;
        return jsonResponse({ detail: 'LAYOUT_REVISION_CONFLICT' }, 409);
      }
      return jsonResponse(putCount === 0 ? persisted(['t0'], 2) : persisted(['server'], 5));
    });

    render(<Harness />);
    await waitFor(() => expect(screen.getByLabelText('tabs')).toHaveTextContent('t0'));
    fireEvent.click(screen.getByRole('button', { name: 'mở tab' }));
    fireEvent.click(screen.getByRole('button', { name: 'lưu layout' }));

    await waitFor(() => expect(screen.getByText('xung đột layout')).toBeVisible());
    // Local work is not thrown away by the failed save.
    expect(screen.getByLabelText('tabs')).toHaveTextContent('t0,new');

    fireEvent.click(screen.getByRole('button', { name: 'tải lại' }));

    await waitFor(() => expect(screen.getByLabelText('tabs')).toHaveTextContent('server'));
    expect(screen.getByLabelText('revision')).toHaveTextContent('bản 5');
    expect(screen.queryByText('xung đột layout')).not.toBeInTheDocument();
  });

  it('does not call the API when disabled', () => {
    const calls = mockFetch(() => jsonResponse(persisted(['t0'], 1)));

    render(<Harness enabled={false} />);

    expect(screen.getByLabelText('tabs')).toHaveTextContent('trống');
    expect(calls).toHaveLength(0);
  });
});
