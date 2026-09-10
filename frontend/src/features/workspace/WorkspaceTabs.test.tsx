import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { WorkspaceTabs, type WorkspaceTabDefinition } from './WorkspaceTabs';

const PROJECT = '018f0000-0000-7000-8000-000000000901';

const TABS: WorkspaceTabDefinition[] = [
  {
    id: '/chapters/c1/translation',
    kind: 'EDITOR',
    title: 'Dịch & hiệu đính',
    chapterId: 'c1',
    to: '/chapters/c1/translation',
  },
  { id: '/chapters/c1/voice', kind: 'PREVIEW', title: 'Giọng đọc', chapterId: 'c1', to: '/chapters/c1/voice' },
  { id: '/jobs', kind: 'JOB', title: 'Công việc', chapterId: null, to: '/jobs' },
];

function jsonResponse(body: unknown, status = 200): Response {
  const text = JSON.stringify(body);
  return { ok: status < 400, status, text: async () => text, json: async () => body } as Response;
}

function mockFetch(handler: (url: string, init?: RequestInit) => Response) {
  const calls: { url: string; init?: RequestInit }[] = [];
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

function emptyLayoutPayload() {
  return { projectId: PROJECT, layout: null, revision: null, migrated: false };
}

function Harness({ dirty = [] as string[] }: { dirty?: string[] }) {
  const [current, setCurrent] = useState(TABS[0].id);
  return (
    <div>
      <p role="status" aria-label="route">
        {current}
      </p>
      <WorkspaceTabs
        projectId={PROJECT}
        openTabs={TABS}
        currentTabId={current}
        dirtyTabIds={dirty}
        onNavigate={setCurrent}
        renderSecondary={(tab) => <p>Nội dung song song: {tab.title}</p>}
      />
    </div>
  );
}

/** The tab list is filled after the layout store resolves, so every test waits for it. */
async function waitForTabs(count = 3): Promise<HTMLElement> {
  const tablist = await screen.findByRole('tablist', { name: 'Tab đang mở' });
  await waitFor(() => expect(within(tablist).getAllByRole('tab')).toHaveLength(count));
  return tablist;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('WorkspaceTabs (U05 round 3)', () => {
  it('shows a tab per visited route, marks the current one selected and navigates on click', async () => {
    mockFetch(() => jsonResponse(emptyLayoutPayload()));

    render(<Harness />);
    const tablist = await waitForTabs();

    expect(within(tablist).getAllByRole('tab')).toHaveLength(3);
    expect(screen.getByRole('tab', { name: 'Dịch & hiệu đính' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByLabelText('Số tab')).toHaveTextContent('3/8');

    fireEvent.click(screen.getByRole('tab', { name: 'Giọng đọc' }));

    expect(screen.getByLabelText('route')).toHaveTextContent('/chapters/c1/voice');
    expect(screen.getByRole('tab', { name: 'Giọng đọc' })).toHaveAttribute('aria-selected', 'true');
  });

  it('supports keyboard navigation across tabs (arrows, Home/End, Enter)', async () => {
    mockFetch(() => jsonResponse(emptyLayoutPayload()));

    render(<Harness />);
    const tablist = await waitForTabs();
    const first = screen.getByRole('tab', { name: 'Dịch & hiệu đính' });
    first.focus();

    fireEvent.keyDown(tablist, { key: 'ArrowRight' });
    expect(screen.getByRole('tab', { name: 'Giọng đọc' })).toHaveFocus();

    fireEvent.keyDown(tablist, { key: 'End' });
    expect(screen.getByRole('tab', { name: 'Công việc' })).toHaveFocus();

    fireEvent.keyDown(tablist, { key: 'Home' });
    expect(first).toHaveFocus();

    fireEvent.keyDown(tablist, { key: 'ArrowLeft' });
    expect(first).toHaveFocus();

    fireEvent.keyDown(tablist, { key: 'Enter' });
    expect(screen.getByLabelText('route')).toHaveTextContent(TABS[0].id);
  });

  it('refuses to close a dirty tab until the user confirms losing the changes', async () => {
    mockFetch(() => jsonResponse(emptyLayoutPayload()));

    render(<Harness dirty={[TABS[1].id]} />);
    await waitForTabs();

    fireEvent.click(screen.getByRole('button', { name: `Đóng tab ${TABS[1].title}` }));

    expect(screen.getByRole('alert')).toHaveTextContent('TAB_DIRTY');
    expect(screen.getByRole('tab', { name: TABS[1].title })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Đóng và bỏ thay đổi' }));

    await waitFor(() => expect(screen.queryByRole('tab', { name: TABS[1].title })).not.toBeInTheDocument());
  });

  it('closes a clean tab and navigates to the neighbouring tab', async () => {
    mockFetch(() => jsonResponse(emptyLayoutPayload()));

    render(<Harness />);
    await waitForTabs();

    fireEvent.click(screen.getByRole('button', { name: `Đóng tab ${TABS[0].title}` }));

    await waitFor(() => expect(screen.queryByRole('tab', { name: TABS[0].title })).not.toBeInTheDocument());
    expect(screen.getByLabelText('route')).toHaveTextContent('/jobs');
  });

  it('docks a tab into the secondary pane and folds it back', async () => {
    mockFetch(() => jsonResponse(emptyLayoutPayload()));

    render(<Harness />);
    await waitForTabs();

    fireEvent.click(screen.getByRole('button', { name: `Dock “${TABS[1].title}”` }));

    const dock = await screen.findByLabelText('Khung phụ');
    expect(within(dock).getByText(`Nội dung song song: ${TABS[1].title}`)).toBeVisible();
    // The docked tab left the primary pane.
    await waitFor(() =>
      expect(
        within(screen.getByRole('tablist', { name: 'Tab đang mở' })).queryByRole('tab', { name: TABS[1].title }),
      ).toBeNull(),
    );

    fireEvent.click(screen.getByRole('button', { name: 'Bỏ dock' }));

    await waitFor(() => expect(screen.getByRole('tab', { name: TABS[1].title })).toBeInTheDocument());
    expect(screen.queryByLabelText('Khung phụ')).not.toBeInTheDocument();
  });

  it('persists the layout with the loaded revision and reports conflicts', async () => {
    const calls = mockFetch((url, init) => {
      if ((init?.method ?? 'GET') === 'PUT') {
        return jsonResponse({ detail: 'LAYOUT_REVISION_CONFLICT' }, 409);
      }
      return jsonResponse({ projectId: PROJECT, layout: null, revision: 4, migrated: false });
    });

    render(<Harness />);
    await waitForTabs();

    fireEvent.click(screen.getByRole('button', { name: 'Lưu layout' }));

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('LAYOUT_REVISION_CONFLICT'));
    const put = calls.find((call) => (call.init?.method ?? 'GET') === 'PUT');
    expect(JSON.parse(String(put?.init?.body))).toMatchObject({ expected_revision: 4 });
  });

  it('keeps working locally when the layout API is not available (local bucket)', async () => {
    const calls = mockFetch(() => jsonResponse({ projectId: 'local', layout: null, revision: null, migrated: false }));

    render(
      <WorkspaceTabs
        projectId="local"
        openTabs={TABS}
        currentTabId={TABS[0].id}
        onNavigate={() => undefined}
      />,
    );

    const tablist = await screen.findByRole('tablist', { name: 'Tab đang mở' });
    await waitFor(() => expect(within(tablist).getAllByRole('tab')).toHaveLength(3));
    expect(calls).toHaveLength(0);
  });
});
