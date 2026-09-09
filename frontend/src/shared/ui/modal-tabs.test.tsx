import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { Modal } from './Modal';
import { Tabs, type Tab } from './Tabs';

afterEach(cleanup);

describe('Modal', () => {
  it('renders a labelled dialog when open and escapes to close', () => {
    const onClose = vi.fn();
    render(
      <Modal open title="Duyệt bản dịch" onClose={onClose}>
        <p>Nội dung duyệt.</p>
      </Modal>,
    );
    const dialog = screen.getByRole('dialog', { name: 'Duyệt bản dịch' });
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    fireEvent.keyDown(dialog, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('does not render when closed', () => {
    render(
      <Modal open={false} title="Đóng" onClose={() => {}}>
        Ẩn.
      </Modal>,
    );
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('closes when the close button is clicked', () => {
    const onClose = vi.fn();
    render(
      <Modal open title="Hủy" onClose={onClose}>
        Nội dung.
      </Modal>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Đóng' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe('Tabs', () => {
  const tabs: Tab[] = [
    { id: 'qa', label: 'QA', content: <span>Danh sách lỗi.</span> },
    { id: 'context', label: 'Bối cảnh', content: <span>Bối cảnh truyện.</span> },
  ];

  it('marks selection and keyboard-navigates with arrow keys', () => {
    const onChange = vi.fn();
    render(<Tabs tabs={tabs} activeId="qa" onChange={onChange} label="Inspector" />);

    const first = screen.getByRole('tab', { name: 'QA' });
    const second = screen.getByRole('tab', { name: 'Bối cảnh' });
    expect(first).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tabpanel').textContent).toContain('Danh sách lỗi.');

    fireEvent.keyDown(first, { key: 'ArrowRight' });
    expect(onChange).toHaveBeenCalledWith('context');
    expect(second).toHaveAttribute('tabindex', '-1');
  });

  it('renders labelled panels that switch content', () => {
    const { rerender } = render(
      <Tabs tabs={tabs} activeId="qa" onChange={() => {}} label="Inspector" />,
    );
    rerender(<Tabs tabs={tabs} activeId="context" onChange={() => {}} label="Inspector" />);
    expect(screen.getByRole('tabpanel').textContent).toContain('Bối cảnh truyện.');
  });
});
