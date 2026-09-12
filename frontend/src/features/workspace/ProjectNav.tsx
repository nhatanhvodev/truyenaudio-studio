import { Link, NavLink } from 'react-router-dom';

import styles from './ProjectNav.module.css';

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
    <nav aria-label="Dự án" className={styles.wrapper}>
      <p className={styles.breadcrumb} aria-label="Đường dẫn">
        <Link to="/" className={styles.crumbLink}>
          Thư viện
        </Link>
        <span aria-hidden="true"> / </span>
        <span aria-current="page" className={styles.crumbCurrent}>
          {projectTitle?.trim() ? projectTitle : projectId}
        </span>
      </p>
      <ul className={styles.areas}>
        {AREAS.map((area) => (
          <li key={area.slug}>
            <NavLink to={`/projects/${projectId}/${area.slug}`} className={styles.area}>
              {area.label}
            </NavLink>
          </li>
        ))}
      </ul>
      <p className={styles.context} aria-label="Ngữ cảnh dự án">
        Đang ở dự án <code>{projectId}</code>
      </p>
    </nav>
  );
}

export default ProjectNav;
