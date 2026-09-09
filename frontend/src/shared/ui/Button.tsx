import type { ButtonHTMLAttributes } from 'react';

import { colors, font, radius, spacing, type UiTheme } from './tokens';

type Variant = 'primary' | 'secondary' | 'danger' | 'ghost';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  theme?: UiTheme;
  loading?: boolean;
}

function backgroundFor(variant: Variant, theme: UiTheme): string {
  const c = colors[theme];
  switch (variant) {
    case 'primary':
      return c.primary;
    case 'danger':
      return c.danger;
    case 'secondary':
      return c.surfaceRaised;
    case 'ghost':
      return 'transparent';
  }
}

function hoverFor(variant: Variant, theme: UiTheme): string {
  const c = colors[theme];
  switch (variant) {
    case 'primary':
      return c.primaryHover;
    case 'danger':
      return c.dangerHover;
    default:
      return c.surfaceRaised;
  }
}

export function Button({
  variant = 'primary',
  theme = 'light',
  loading = false,
  disabled,
  children,
  style,
  ...rest
}: ButtonProps) {
  const c = colors[theme];
  const isDisabled = Boolean(disabled || loading);
  const background = backgroundFor(variant, theme);
  const foreground =
    variant === 'primary' || variant === 'danger' ? c.textOnPrimary : c.text;
  return (
    <button
      type="button"
      {...rest}
      disabled={isDisabled}
      aria-busy={loading || undefined}
      style={{
        background,
        color: foreground,
        border:
          variant === 'secondary' || variant === 'ghost'
            ? `1px solid ${c.border}`
            : 'none',
        borderRadius: radius.md,
        padding: `${spacing.sm}px ${spacing.lg}px`,
        fontFamily: 'inherit',
        fontSize: font.sizeMd,
        fontWeight: font.weightMedium,
        lineHeight: 1.25,
        cursor: isDisabled ? 'not-allowed' : 'pointer',
        opacity: isDisabled ? 0.55 : 1,
        outline: 'none',
        ...style,
      }}
      onMouseEnter={(event) => {
        if (!isDisabled) {
          event.currentTarget.style.background = hoverFor(variant, theme);
        }
      }}
      onMouseLeave={(event) => {
        event.currentTarget.style.background = background;
      }}
      onFocus={(event) => {
        event.currentTarget.style.boxShadow = `0 0 0 3px ${c.focusRing}`;
      }}
      onBlur={(event) => {
        event.currentTarget.style.boxShadow = 'none';
      }}
    >
      {children}
    </button>
  );
}
