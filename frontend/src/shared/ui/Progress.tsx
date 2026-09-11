import styles from './Progress.module.css';

export interface ProgressProps {
  value: number;
  max?: number;
  label?: string;
}

export function Progress({ value, max = 100, label = 'Tiến độ' }: ProgressProps) {
  const bounded = Math.max(0, Math.min(max, value));
  const percent = max > 0 ? Math.round((bounded / max) * 100) : 0;
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuenow={bounded}
      className={styles.track}
    >
      <div className={styles.bar} style={{ width: `${percent}%` }} />
    </div>
  );
}
