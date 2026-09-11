import { useId, type InputHTMLAttributes, type ReactNode } from 'react';

import styles from './Input.module.css';

export interface InputProps
  extends Omit<InputHTMLAttributes<HTMLInputElement>, 'id'> {
  label: string;
  error?: string | null;
  hint?: ReactNode;
}

export function Input({
  label,
  error,
  hint,
  required,
  className,
  ...rest
}: InputProps) {
  const autoId = useId();
  const id = autoId;
  const errorId = error ? `${id}-error` : undefined;
  const hintId = hint ? `${id}-hint` : undefined;
  const describedBy = [errorId, hintId].filter(Boolean).join(' ') || undefined;
  return (
    <>
      <label htmlFor={id} className={styles.label}>
        {label}
        {required ? (
          <span className={styles.required} aria-hidden="true">
            *
          </span>
        ) : null}
      </label>
      <input
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        required={required}
        {...rest}
        className={[styles.field, error ? styles.invalid : undefined, className]
          .filter(Boolean)
          .join(' ')}
      />
      {error ? (
        <span id={errorId} role="alert" className={styles.error}>
          {error}
        </span>
      ) : null}
      {hint ? (
        <span id={hintId} className={styles.description}>
          {hint}
        </span>
      ) : null}
    </>
  );
}
