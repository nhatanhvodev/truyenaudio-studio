import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import RepairDiff from './RepairDiff';

afterEach(() => {
  cleanup();
});

describe('RepairDiff', () => {
  it('shows word-level replacement without accepting automatically', () => {
    const onAccept = vi.fn();
    const onReject = vi.fn();

    render(
      <RepairDiff
        proposal={{
          id: 'proposal-1',
          baseRunId: 'run-1',
          estimatedCostVnd: 123,
          hash: 'h'.repeat(64),
          replacements: [
            {
              sourceSegmentId: 'source-1',
              sourceText: '林动 opened the door.',
              currentTargetText: 'Lam Dong mo cua sai.',
              targetText: 'Lam Dong mo cua dung.',
            },
          ],
        }}
        onAccept={onAccept}
        onReject={onReject}
      />,
    );

    expect(screen.getByLabelText('Current target')).toHaveTextContent('Lam Dong mo cua sai.');
    expect(screen.getByLabelText('Proposed target')).toHaveTextContent('Lam Dong mo cua dung.');
    expect(onAccept).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Accept repair' }));

    expect(onAccept).toHaveBeenCalledWith('proposal-1', 'h'.repeat(64));
    expect(onReject).not.toHaveBeenCalled();
  });

  it('declares an offline-generated proposal before it can be accepted', () => {
    render(
      <RepairDiff
        proposal={{
          id: 'proposal-offline',
          baseRunId: 'run-1',
          estimatedCostVnd: 0,
          generator: 'OFFLINE_DETERMINISTIC',
          hash: 'c'.repeat(64),
          replacements: [],
        }}
        onAccept={vi.fn()}
        onReject={vi.fn()}
      />,
    );

    // Applying a proposal writes a new translation run, so the operator must see
    // that this one did not come from a cloud model.
    expect(screen.getByTestId('repair-generator-note')).toHaveTextContent(/offline tất định/);
    expect(screen.getByTestId('repair-generator-note')).toHaveTextContent(/J01/);
  });

  it('warns instead of staying silent when the payload does not name a generator', () => {
    render(
      <RepairDiff
        proposal={{
          id: 'proposal-3',
          baseRunId: 'run-1',
          estimatedCostVnd: 0,
          hash: 'b'.repeat(64),
          replacements: [],
        }}
        onAccept={vi.fn()}
        onReject={vi.fn()}
      />,
    );

    // Fail-closed: an unnamed generator must not reach apply without a caution.
    expect(screen.getByTestId('repair-generator-note')).toHaveTextContent(/Chưa xác định được nguồn sinh/);
  });

  it('rejects a proposal without accepting it', () => {
    const onAccept = vi.fn();
    const onReject = vi.fn();

    render(
      <RepairDiff
        proposal={{
          id: 'proposal-2',
          baseRunId: 'run-1',
          estimatedCostVnd: 0,
          hash: 'a'.repeat(64),
          replacements: [],
        }}
        onAccept={onAccept}
        onReject={onReject}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Reject' }));

    expect(onReject).toHaveBeenCalledWith('proposal-2');
    expect(onAccept).not.toHaveBeenCalled();
  });
});
