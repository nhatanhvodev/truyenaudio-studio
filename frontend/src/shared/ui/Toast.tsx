import styles from './Toast.module.css';

export interface ToastProps {
  message: string;
  tone?: 'info' | 'success' | 'danger';
}

export function Toast({ message, tone = 'info' }: ToastProps) {
  const toneClass =
    tone === 'success'
      ? styles.toneSuccess
      : tone === 'danger'
        ? styles.toneDanger
        : null;
  return (
    <div
      role="status"
      aria-live="polite"
      className={toneClass ? `${styles.toast} ${toneClass}` : styles.toast}
    >
      {message}
    </div>
  );
}
