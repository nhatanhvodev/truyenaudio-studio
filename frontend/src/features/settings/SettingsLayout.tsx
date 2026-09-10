import { NavLink, Outlet, useLocation } from 'react-router-dom';

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
    <section style={{ display: 'flex', gap: 24, padding: 24, alignItems: 'flex-start' }}>
      <nav aria-label="Nhóm cài đặt" style={{ minWidth: 220 }}>
        <h1 style={{ fontSize: 16, margin: '0 0 12px' }}>Cài đặt</h1>
        <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'grid', gap: 4 }}>
          {SETTINGS_GROUPS.map((group) => (
            <li key={group.slug}>
              <NavLink
                to={`/settings/${group.slug}`}
                title={group.hint}
                style={({ isActive }) => ({
                  display: 'block',
                  padding: '6px 10px',
                  borderRadius: 6,
                  textDecoration: 'none',
                  fontWeight: isActive ? 600 : 400,
                  color: isActive ? '#1d4ed8' : '#4b5563',
                })}
              >
                {group.label}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
      <div style={{ flex: 1, minWidth: 0 }}>
        <p aria-label="Đường dẫn" style={{ margin: '0 0 12px', color: '#4b5563', fontSize: 13 }}>
          Cài đặt{active ? ` / ${active.label}` : ''}
        </p>
        <Outlet />
      </div>
    </section>
  );
}
