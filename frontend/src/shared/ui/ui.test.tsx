import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { Button } from './Button';
import { Input } from './Input';
import { Select } from './Select';

afterEach(cleanup);

describe('shared UI primitives (U01 part 1)', () => {
  it('Button disables while loading and keeps its label', () => {
    render(
      <Button loading onClick={() => {}}>
        Dịch chương
      </Button>,
    );
    const button = screen.getByRole('button', { name: 'Dịch chương' });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('aria-busy', 'true');
  });

  it('Button exposes a keyboard-focusable default type without submitting', () => {
    render(<Button>Lưu</Button>);
    const button = screen.getByRole('button', { name: 'Lưu' });
    expect(button).toHaveAttribute('type', 'button');
    button.focus();
    expect(button).toHaveFocus();
  });

  it('Input wires label, error and description ids for assistive tech', () => {
    render(
      <Input
        label="Tên dự án"
        value="Truyện A"
        error="Không được bỏ trống."
        onChange={() => {}}
      />,
    );
    const input = screen.getByLabelText('Tên dự án');
    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(input.getAttribute('aria-describedby')).toBeTruthy();
    expect(screen.getByRole('alert').textContent).toBe('Không được bỏ trống.');
  });

  it('Input marks required fields for the label', () => {
    render(<Input label="Giọng đọc" required />);
    expect(screen.getByText('*')).toBeTruthy();
    expect(screen.getByLabelText(/Giọng đọc/)).toHaveAttribute('required');
  });

  it('Select renders options and reports errors via alert role', () => {
    render(
      <Select
        label="Provider"
        options={[
          { value: 'gemini', label: 'Gemini' },
          { value: 'qwen', label: 'Qwen' },
        ]}
        error="Cần chọn provider."
      />,
    );
    const select = screen.getByLabelText('Provider');
    expect(screen.getAllByRole('option')).toHaveLength(2);
    fireEvent.change(select, { target: { value: 'qwen' } });
    expect(screen.getByRole('alert').textContent).toBe('Cần chọn provider.');
  });
});
