import { useId, type InputHTMLAttributes, type ReactNode } from 'react';

import { colors, font, radius, spacing, type UiTheme } from './tokens';

export interface InputProps
  extends Omit<InputHTMLAttributes<HTMLInputElement>, 'id'> {
  label: string;
  theme?: UiTheme;
  error?: string | null;
  hint?: ReactNode;
}

export function Input({
  label,
  theme = 'light',
  error,
  hint,
  required,
  style,
  ...rest
}: InputProps) {
  const autoId = useId();
  const id = autoId;
  const errorId = error ? `${id}-error` : undefined;
  const hintId = hint ? `${id}-hint` : undefined;
  const c = colors[theme];
  const describedBy = [errorId, hintId].filter(Boolean).join(' ') || undefined;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: spacing.xs }}>
      <label
        htmlFor={id}
        style={{ fontSize: font.sizeSm, fontWeight: font.weightMedium, color: c.text }}
      >
        {label}
        {required ? <span aria-hidden="true"> *</span> : null}
      </label>
      <input
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
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
        onFocus={(event) => {
          event.currentTarget.style.boxShadow = `0 0 0 2px ${c.focusRing}`;
        }}
        onBlur={(event) => {
          event.currentTarget.style.boxShadow = 'none';
        }}
      />
      {error ? (
        <span id={errorId} role="alert" style={{ color: c.danger, fontSize: font.sizeSm }}>
          {error}
        </span>
      ) : null}
      {hint ? (
        <span id={hintId} style={{ color: c.textMuted, fontSize: font.sizeSm }}>
          {hint}
        </span>
      ) : null}
    </div>
  );
}
