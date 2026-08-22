import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import AudioReview from './AudioReview';

afterEach(() => cleanup());

describe('AudioReview ASR advisory', () => {
  it('shows advisory ASR issues without disabling approve by itself', () => {
    render(
      <AudioReview
        chapterId="chapter-1"
        presetId="voice-1"
        initialRendered={{
          masterArtifactId: 'master-1',
          masterSha256: 'abcdef1234567890',
          reusedSegmentIds: [],
          renderedSegmentIds: ['seg-1'],
        }}
        asrIssues={[
          { id: 'issue-1', category: 'NUMBER_UNIT', severity: 'MAJOR', segmentId: 'seg-1', evidence: 'Thiếu số 12' },
        ]}
      />,
    );

    expect(screen.getByText('ASR advisory')).toBeVisible();
    expect(screen.getByText('NUMBER_UNIT · MAJOR · seg-1')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Approve' })).toBeEnabled();
  });
});
