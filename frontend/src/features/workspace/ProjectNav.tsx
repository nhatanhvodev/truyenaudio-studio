import { Link, NavLink } from 'react-router-dom';

type Props = {
  projectId: string;
  /** Project title when known; the id is used as a stable fallback. */
  projectTitle?: string | null;
};

const AREAS = [
  { slug: 'import', label: 'Nhập nội dung' },
  { slug: 'batch', label: 'Hàng đợi batch' },
  { slug: 'settings', label: 'Cài đặt dự án' },
] as const;

/**
 * U02: project-scoped navigation and breadcrumb.
 *
 * The project id comes from the URL, so a deep link (or the browser Back button)
 * always lands inside the same project; the breadcrumb keeps that context
 * visible. Every item is a real link, so keyboard navigation is the platform's
 * (Tab to move, Enter to follow) and `aria-current` marks the active area.
 */
export function ProjectNav({ projectId, projectTitle }: Props) {
  return (
    <nav aria-label="Dự án" style={styles.shell}>
      <p style={styles.breadcrumb} aria-label="Đường dẫn">
        <Link to="/" style={styles.crumbLink}>
          Thư viện
        </Link>
        <span aria-hidden="true"> / </span>
        <span aria-current="page" style={styles.crumbCurrent}>
          {projectTitle?.trim() ? projectTitle : projectId}
        </span>
      </p>
      <ul style={styles.list}>
        {AREAS.map((area) => (
          <li key={area.slug}>
            <NavLink
              to={`/projects/${projectId}/${area.slug}`}
              style={({ isActive }) => ({ ...styles.link, ...(isActive ? styles.linkActive : {}) })}
            >
              {area.label}
            </NavLink>
          </li>
        ))}
      </ul>
      <p style={styles.context} aria-label="Ngữ cảnh dự án">
        Đang ở dự án <code>{projectId}</code>
      </p>
    </nav>
  );
}

export default ProjectNav;

const styles: Record<string, React.CSSProperties> = {
  shell: { display: 'grid', gap: 6, margin: '0 auto 12px', maxWidth: 920 },
  breadcrumb: { margin: 0, fontSize: 13, color: '#475467' },
  crumbLink: { color: '#155eef', fontWeight: 700, textDecoration: 'none' },
  crumbCurrent: { fontWeight: 700, color: '#18212f' },
  list: { display: 'flex', gap: 12, listStyle: 'none', margin: 0, padding: 0 },
  link: { color: '#475467', fontWeight: 600, textDecoration: 'none' },
  linkActive: { color: '#1d4ed8', fontWeight: 800, textDecoration: 'underline' },
  context: { margin: 0, fontSize: 12, color: '#667085' },
};
