import { useState } from 'react';
import { Link } from 'react-router-dom';

import { acknowledgeDarkDefaultNotice, shouldAnnounceDarkDefault } from './themeRuntime';
import styles from './ThemeChangeNotice.module.css';

/**
 * One-time notice for the only cohort the redesign's dark default actually
 * changes: a browser with no stored preference document whose operating system
 * is light. `shouldAnnounceDarkDefault` owns that rule and documents why the
 * other cohorts are excluded — including why an OS-dark user, for whom nothing
 * changed, must not be told that something did.
 *
 * The decision is made in the state INITIALISER rather than in an effect, so the
 * notice is part of the first commit and cannot shift the layout after paint.
 */
export function ThemeChangeNotice() {
  const [visible, setVisible] = useState(() => shouldAnnounceDarkDefault());

  if (!visible) return null;

  return (
    <div className={styles.notice}>
      <p className={styles.text}>
        Giao diện mặc định đã chuyển sang bản tối. Bạn có thể đổi lại trong{' '}
        <Link to="/settings/appearance" className={styles.link}>
          Cài đặt › Appearance
        </Link>
        .
      </p>
      <button
        type="button"
        aria-label="Đóng thông báo giao diện"
        className={styles.dismiss}
        onClick={() => {
          acknowledgeDarkDefaultNotice();
          setVisible(false);
        }}
      >
        ×
      </button>
    </div>
  );
}
