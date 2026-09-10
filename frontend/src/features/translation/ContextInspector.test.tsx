import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import ContextInspector, { type ContextTrace } from './ContextInspector';

function trace(overrides: Partial<ContextTrace> = {}): ContextTrace {
  return {
    chapterId: 'chapter-1',
    projectId: 'project-1',
    ordinal: 3,
    state: 'TRANSLATION_REVIEW',
    run: {
      id: 'run-1',
      status: 'REVIEW',
      model: 'fake-hanviet-v2',
      providerProfileId: 'profile-1',
      promptVersion: 'translation-v1',
      glossaryRevisionHash: 'a'.repeat(64),
      storyMemoryRevisionHash: 'b'.repeat(64),
      sourceRevisionId: 'rev-1',
    },
    glossary: {
      sha256: 'a'.repeat(64),
      entryCount: 2,
      lockedRules: [{ sourceTerm: '林动', targetTerm: 'Lâm Động', forbiddenForms: ['Lâm Đông'] }],
    },
    memory: {
      sha256: 'b'.repeat(64),
      entries: [
        {
          id: 'memory-1',
          entityKey: 'lin-dong',
          entityType: 'CHARACTER',
          summary: 'Nhân vật chính.',
          validFromOrdinal: 1,
          validToOrdinal: null,
          status: 'APPROVED',
          sourceRunId: 'run-0',
        },
      ],
    },
    characters: [
      {
        characterId: 'character-1',
        revisionId: 'revision-1',
        revisionNo: 2,
        canonicalName: 'Lâm Động',
        aliases: ['Động ca'],
        entityType: 'PERSON',
        status: 'APPROVED',
      },
    ],
    stale: { glossary: false, memory: false },
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
});

describe('ContextInspector (U06 round 2)', () => {
  it('shows the glossary rules, approved memory and characters used by the run', async () => {
    render(<ContextInspector chapterId="chapter-1" loadTrace={async () => trace()} />);

    const inspector = await screen.findByLabelText('Context inspector');
    expect(within(inspector).getByText(/Glossary \(2 mục\)/)).toBeVisible();
    expect(within(inspector).getByText(/林动 → Lâm Động \(cấm: Lâm Đông\)/)).toBeVisible();
    expect(within(inspector).getByText(/lin-dong · Nhân vật chính\./)).toBeVisible();
    expect(within(inspector).getByText(/Lâm Động \(Động ca\) · APPROVED/)).toBeVisible();
    expect(within(inspector).getByLabelText('Lượt dịch')).toHaveTextContent('REVIEW');
    expect(within(inspector).getByText('Ngữ cảnh khớp với lượt dịch gần nhất.')).toBeVisible();
  });

  it('warns when the run was produced with an older glossary or memory', async () => {
    render(
      <ContextInspector
        chapterId="chapter-1"
        loadTrace={async () => trace({ stale: { glossary: true, memory: true } })}
      />,
    );

    const alert = await screen.findByRole('alert');

    expect(alert).toHaveTextContent('Ngữ cảnh đã thay đổi sau lượt dịch này');
    expect(alert).toHaveTextContent('glossary, bộ nhớ truyện');
  });

  it('keeps raw ids in the details drawer instead of the working view', async () => {
    render(<ContextInspector chapterId="chapter-1" loadTrace={async () => trace()} />);
    await screen.findByLabelText('Context inspector');

    // The working view shows the labels, not the identifiers.
    expect(screen.queryByText('memory-1')).not.toBeInTheDocument();
    expect(screen.queryByText('revision-1')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Chi tiết ID' }));

    const drawer = await screen.findByRole('dialog', { name: 'ID chi tiết (ngữ cảnh)' });
    expect(within(drawer).getByText('run-1')).toBeVisible();
    expect(within(drawer).getByText('memory-1')).toBeVisible();
    expect(within(drawer).getByText('revision-1')).toBeVisible();
    expect(within(drawer).getByText('profile-1')).toBeVisible();

    fireEvent.click(within(drawer).getByRole('button', { name: 'Đóng' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('explains an empty context and a chapter without a run', async () => {
    render(
      <ContextInspector
        chapterId="chapter-2"
        loadTrace={async () =>
          trace({
            run: null,
            glossary: { sha256: 'c'.repeat(64), entryCount: 0, lockedRules: [] },
            memory: { sha256: 'd'.repeat(64), entries: [] },
            characters: [],
          })
        }
      />,
    );

    const inspector = await screen.findByLabelText('Context inspector');
    expect(within(inspector).getByText('Chương này chưa có lượt dịch nào.')).toBeVisible();
    expect(within(inspector).getByText('Không có thuật ngữ khóa trong phạm vi chương này.')).toBeVisible();
    expect(within(inspector).getByText('Chưa có mục bộ nhớ nào được duyệt cho chương này.')).toBeVisible();
    expect(within(inspector).getByText('Chưa có nhân vật nào.')).toBeVisible();
  });

  it('surfaces a trace failure instead of rendering an empty inspector', async () => {
    const loadTrace = vi.fn(async () => {
      throw new Error('CHAPTER_NOT_FOUND');
    });

    render(<ContextInspector chapterId="missing" loadTrace={loadTrace} />);

    expect(await screen.findByRole('alert')).toHaveTextContent('CHAPTER_NOT_FOUND');
    expect(screen.queryByLabelText('Context inspector')).not.toBeInTheDocument();
  });
});
