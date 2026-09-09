import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { Combobox } from './Combobox';
import { Drawer } from './Drawer';

afterEach(cleanup);

describe('Combobox', () => {
  const options = [
    { value: 'gemini', label: 'Gemini' },
    { value: 'qwen', label: 'Qwen' },
    { value: 'openrouter', label: 'OpenRouter' },
  ];

  it('opens a listbox and selects an option with the keyboard', () => {
    const onChange = vi.fn();
    render(<Combobox label="Provider" options={options} value="" onChange={onChange} />);

    const input = screen.getByRole('combobox', { name: 'Provider' });
    fireEvent.focus(input);
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    fireEvent.keyDown(input, { key: 'Enter' });

    expect(onChange).toHaveBeenCalledWith('openrouter');
  });

  it('filters options as the user types', () => {
    const onChange = vi.fn();
    render(<Combobox label="Provider" options={options} value="" onChange={onChange} />);

    const input = screen.getByRole('combobox', { name: 'Provider' });
    fireEvent.change(input, { target: { value: 'qw' } });
    fireEvent.keyDown(input, { key: 'ArrowDown' });

    const visibleOptions = screen.getAllByRole('option');
    expect(visibleOptions.length).toBeGreaterThanOrEqual(1);
    expect(visibleOptions[0].textContent).toBe('Qwen');
  });

  it('closes on Escape', () => {
    render(<Combobox label="Provider" options={options} value="" onChange={() => {}} />);
    const input = screen.getByRole('combobox', { name: 'Provider' });
    fireEvent.focus(input);
    expect(screen.queryByRole('listbox')).toBeTruthy();
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(screen.queryByRole('listbox')).toBeNull();
  });
});

describe('Drawer', () => {
  it('renders a labelled modal dialog and closes on Escape', () => {
    const onClose = vi.fn();
    render(
      <Drawer open title="Inspector" onClose={onClose}>
        <p>Bá»‘i cáº£nh.</p>
      </Drawer>,
    );
    const dialog = screen.getByRole('dialog', { name: 'Inspector' });
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    fireEvent.keyDown(dialog, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('renders nothing when closed', () => {
    render(
      <Drawer open={false} title="Inspector" onClose={() => {}}>
        áº¨n.
      </Drawer>,
    );
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});
