import { NavLink, Outlet, useLocation } from 'react-router-dom';

import styles from './SettingsLayout.module.css';

export const SETTINGS_GROUPS = [
  { slug: 'providers', label: 'AI Providers', hint: 'Credential và trạng thái kết nối' },
  { slug: 'models', label: 'Models', hint: 'Catalog, filter và nguồn dữ liệu' },
  { slug: 'translation', label: 'Translation', hint: 'Ngôn ngữ, thể loại, style, quality' },
  { slug: 'tts', label: 'TTS', hint: 'Engine, giọng đọc và preview' },
  { slug: 'storage', label: 'Storage', hint: 'Dung lượng, backup, restore, retention' },
  { slug: 'appearance', label: 'Appearance', hint: 'Theme, font, mật độ hiển thị' },
  { slug: 'advanced', label: 'Advanced', hint: 'Diagnostics và feature flags' },
] as const;

export function SettingsLayout() {
  const location = useLocation();
  const active = SETTINGS_GROUPS.find((group) => location.pathname.endsWith(`/${group.slug}`));

  return (
    <section className={styles.shell}>
      <nav aria-label="Nhóm cài đặt" className={styles.nav}>
        <h1 className={styles.heading}>Cài đặt</h1>
        <ul className={styles.groups}>
          {SETTINGS_GROUPS.map((group) => (
            <li key={group.slug}>
              <NavLink to={`/settings/${group.slug}`} title={group.hint} className={styles.item}>
                {group.label}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
      <div className={styles.main}>
        <p aria-label="Đường dẫn" className={styles.crumb}>
          Cài đặt{active ? ` / ${active.label}` : ''}
        </p>
        <Outlet />
      </div>
    </section>
  );
}
