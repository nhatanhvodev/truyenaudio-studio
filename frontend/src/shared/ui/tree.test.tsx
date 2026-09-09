import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { Tree, type TreeNode } from './Tree';

afterEach(cleanup);

const nodes: TreeNode[] = [
  {
    id: 'project',
    label: 'Dự án A',
    children: [
      { id: 'ch1', label: 'Chương 1' },
      { id: 'ch2', label: 'Chương 2' },
    ],
  },
];

describe('Tree', () => {
  it('renders a labelled tree with nested groups', () => {
    render(<Tree nodes={nodes} label="Mục lục" />);
    expect(screen.getByRole('tree', { name: 'Mục lục' })).toBeTruthy();
    expect(screen.getAllByRole('treeitem')).toHaveLength(3);
  });

  it('collapses and expands a branch', () => {
    render(<Tree nodes={nodes} label="Mục lục" />);
    const toggle = screen.getByRole('button', { name: 'Đóng Dự án A' });
    fireEvent.click(toggle);
    expect(screen.getAllByRole('treeitem')).toHaveLength(1);
    const reopen = screen.getByRole('button', { name: 'Mở Dự án A' });
    fireEvent.click(reopen);
    expect(screen.getAllByRole('treeitem')).toHaveLength(3);
  });

  it('selects a node when the callback is provided', () => {
    const onSelect = vi.fn();
    render(<Tree nodes={nodes} label="Mục lục" onSelect={onSelect} selectedId="ch1" />);
    fireEvent.click(screen.getByText('Chương 1'));
    expect(onSelect).toHaveBeenCalledWith('ch1');
  });
});
