import { NavLink } from 'react-router-dom';

import styles from './GlobalNav.module.css';

interface Area {
  to: string;
  label: string;
  hint: string;
  end?: boolean;
}

const AREAS: readonly Area[] = [
  { to: '/', label: 'Thư viện', hint: 'Danh sách dự án và chương', end: true },
  { to: '/jobs', label: 'Công việc', hint: 'Job đang chạy, tiến độ và lỗi' },
  { to: '/settings', label: 'Cài đặt', hint: 'Provider, model, style, TTS, storage' },
];

export function GlobalNav() {
  return (
    <nav aria-label="Khu vực" className={styles.nav}>
      {AREAS.map((area) => (
        <NavLink
          key={area.to}
          to={area.to}
          end={area.end}
          title={area.hint}
          className={styles.link}
        >
          {area.label}
        </NavLink>
      ))}
    </nav>
  );
}
