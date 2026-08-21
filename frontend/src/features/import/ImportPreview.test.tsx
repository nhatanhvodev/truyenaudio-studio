import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ImportPreview from './ImportPreview';

afterEach(() => {
  cleanup();
});

describe('ImportPreview', () => {
  it('shows ordered candidates, warnings, and confirms the preview only on user action', () => {
    const onConfirm = vi.fn();
    const candidates = [
      {
        ordinal: 2,
        title: 'Chuong 2',
        text: 'Noi dung hai.',
        sourcePath: 'chapter-2.txt',
        warnings: [],
      },
      {
        ordinal: null,
        title: null,
        text: '',
        sourcePath: 'empty.txt',
        warnings: ['ORDINAL_MISSING', 'EMPTY_CHAPTER'],
      },
    ];

    render(<ImportPreview candidates={candidates} onConfirm={onConfirm} />);

    expect(screen.getByText('chapter-2.txt')).toBeVisible();
    expect(screen.getByText('empty.txt')).toBeVisible();
    expect(screen.getByText('ORDINAL_MISSING')).toBeVisible();
    expect(screen.getByText('EMPTY_CHAPTER')).toBeVisible();
    expect(onConfirm).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Confirm import mapping' }));

    expect(onConfirm).toHaveBeenCalledWith(candidates);
  });
});
