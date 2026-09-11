import { useId, type SelectHTMLAttributes } from 'react';

import styles from './Select.module.css';

export interface SelectProps
  extends Omit<SelectHTMLAttributes<HTMLSelectElement>, 'id'> {
  label: string;
  error?: string | null;
  options: readonly { value: string; label: string }[];
}

export function Select({
  label,
  error,
  options,
  required,
  className,
  ...rest
}: SelectProps) {
  const autoId = useId();
  const id = autoId;
  const errorId = error ? `${id}-error` : undefined;
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
      <select
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={errorId}
        required={required}
        {...rest}
        className={[styles.select, error ? styles.invalid : undefined, className]
          .filter(Boolean)
          .join(' ')}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value} className={styles.option}>
            {option.label}
          </option>
        ))}
      </select>
      {error ? (
        <span id={errorId} role="alert" className={styles.error}>
          {error}
        </span>
      ) : null}
    </>
  );
}
