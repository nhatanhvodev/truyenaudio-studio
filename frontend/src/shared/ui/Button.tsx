import type { ButtonHTMLAttributes } from 'react';

import styles from './Button.module.css';

type Variant = 'primary' | 'secondary' | 'danger' | 'ghost';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  loading?: boolean;
}

export function Button({
  variant = 'primary',
  loading = false,
  disabled,
  children,
  className,
  ...rest
}: ButtonProps) {
  const isDisabled = Boolean(disabled || loading);
  return (
    <button
      type="button"
      {...rest}
      className={[styles.button, styles[variant], className].filter(Boolean).join(' ')}
      disabled={isDisabled}
      aria-busy={loading || undefined}
    >
      {children}
    </button>
  );
}
