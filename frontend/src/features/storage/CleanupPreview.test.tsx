import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { CleanupPreview } from './CleanupPreview';

afterEach(() => {
  cleanup();
});

describe('CleanupPreview', () => {
  it('renders reviewed candidates and executes with the plan snapshot token', () => {
    const onExecute = vi.fn();
    render(
      <CleanupPreview
        plan={{
          planId: 'plan-1',
          snapshotHash: 'a'.repeat(64),
          totalBytes: 12,
          candidates: [
            {
              candidateType: 'unapproved_preview',
              relativePath: 'previews/old.wav',
              byteSize: 12,
              sha256: 'b'.repeat(64),
            },
          ],
        }}
        onExecute={onExecute}
      />,
    );

    expect(screen.getByText('previews/old.wav')).toBeVisible();
    expect(screen.getByText('12 bytes')).toBeVisible();

    fireEvent.click(screen.getByRole('button', { name: 'Delete reviewed files' }));

    expect(onExecute).toHaveBeenCalledWith('plan-1', 'a'.repeat(64));
  });
});
