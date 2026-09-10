import { NavLink } from 'react-router-dom';

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
    <nav aria-label="Khu vực" style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
      {AREAS.map((area) => (
        <NavLink
          key={area.to}
          to={area.to}
          end={area.end}
          title={area.hint}
          style={({ isActive }) => ({
            textDecoration: 'none',
            fontWeight: isActive ? 600 : 400,
            color: isActive ? '#1d4ed8' : '#4b5563',
          })}
        >
          {area.label}
        </NavLink>
      ))}
    </nav>
  );
}
