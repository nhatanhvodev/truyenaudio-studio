import { useId, type SelectHTMLAttributes } from 'react';

import { colors, font, radius, spacing, type UiTheme } from './tokens';

export interface SelectProps
  extends Omit<SelectHTMLAttributes<HTMLSelectElement>, 'id'> {
  label: string;
  theme?: UiTheme;
  error?: string | null;
  options: readonly { value: string; label: string }[];
}

export function Select({
  label,
  theme = 'light',
  error,
  options,
  required,
  style,
  ...rest
}: SelectProps) {
  const autoId = useId();
  const id = autoId;
  const errorId = error ? `${id}-error` : undefined;
  const c = colors[theme];
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: spacing.xs }}>
      <label
        htmlFor={id}
        style={{ fontSize: font.sizeSm, fontWeight: font.weightMedium, color: c.text }}
      >
        {label}
        {required ? <span aria-hidden="true"> *</span> : null}
      </label>
      <select
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={errorId}
        required={required}
        {...rest}
        style={{
          background: c.surface,
          color: c.text,
          border: `1px solid ${error ? c.danger : c.border}`,
          borderRadius: radius.sm,
          padding: `${spacing.sm}px ${spacing.md}px`,
          fontSize: font.sizeMd,
          fontFamily: 'inherit',
          outline: 'none',
          ...style,
        }}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      {error ? (
        <span id={errorId} role="alert" style={{ color: c.danger, fontSize: font.sizeSm }}>
          {error}
        </span>
      ) : null}
    </div>
  );
}
