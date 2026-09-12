import { useState, type ReactNode } from 'react';

import styles from './ProjectScopedPanel.module.css';

export interface ProjectScopedPanelProps {
  title: string;
  note: string;
  render: (projectId: string) => ReactNode;
}

/**
 * Project-scoped settings need an explicit project; this gate asks for the
 * project id (kept only in component state — nothing is persisted locally)
 * before rendering the manager for that project.
 */
export function ProjectScopedPanel({ title, note, render }: ProjectScopedPanelProps) {
  const [projectId, setProjectId] = useState('');

  return (
    <section aria-labelledby="project-scoped-heading" className={styles.panel}>
      <h2 id="project-scoped-heading" className={styles.panelHeading}>
        {title}
      </h2>
      <p className={styles.panelNote}>{note}</p>
      <label className={styles.field}>
        Project ID
        <input
          className={styles.input}
          value={projectId}
          onChange={(event) => setProjectId(event.target.value.trim())}
          placeholder="018f0000-…"
        />
      </label>
      {projectId ? (
        render(projectId)
      ) : (
        <p className={styles.hint}>Nhập project ID để quản lý cấu hình theo project.</p>
      )}
    </section>
  );
}
