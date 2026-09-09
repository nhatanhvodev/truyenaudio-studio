import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { Progress } from './Progress';
import { Table } from './Table';
import { Toast } from './Toast';
import { Tooltip } from './Tooltip';

afterEach(cleanup);

describe('Tooltip', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  it('announces content via aria-describedby on focus', () => {
    render(
      <Tooltip content="Giải thích thuật ngữ">
        <button type="button">Tên riêng</button>
      </Tooltip>,
    );
    const trigger = screen.getByRole('button', { name: 'Tên riêng' });
    fireEvent.focus(trigger);
    act(() => {
      vi.advanceTimersByTime(130);
    });
    expect(screen.getByRole('tooltip').textContent).toBe('Giải thích thuật ngữ');
    expect(trigger.getAttribute('aria-describedby')).toBeTruthy();
  });
});

describe('Toast', () => {
  it('renders a polite live region for screen readers', () => {
    render(<Toast message="Đã lưu bản dịch." tone="success" />);
    const toast = screen.getByRole('status');
    expect(toast.textContent).toBe('Đã lưu bản dịch.');
    expect(toast).toHaveAttribute('aria-live', 'polite');
  });
});

describe('Progress', () => {
  it('exposes progressbar semantics with numeric value', () => {
    render(<Progress value={42} max={100} label="Dịch chương" />);
    const bar = screen.getByRole('progressbar', { name: 'Dịch chương' });
    expect(bar).toHaveAttribute('aria-valuenow', '42');
    expect(bar).toHaveAttribute('aria-valuemax', '100');
  });
});

describe('Table', () => {
  it('renders semantic headers and caption', () => {
    render(
      <Table
        caption="Danh sách chương"
        columns={[
          { key: 'title', header: 'Tiêu đề', render: (row) => row.title },
        ]}
        rows={[{ id: 'ch1', title: 'Chương một' }]}
      />,
    );
    expect(screen.getByText('Danh sách chương')).toBeTruthy();
    expect(screen.getByRole('columnheader', { name: 'Tiêu đề' })).toBeTruthy();
    expect(screen.getByRole('cell')).toHaveTextContent('Chương một');
  });
});
