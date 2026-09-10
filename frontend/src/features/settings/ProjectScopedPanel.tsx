import { useState, type ReactNode } from 'react';

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
    <section aria-labelledby="project-scoped-heading">
      <h2 id="project-scoped-heading" style={{ margin: '0 0 8px', fontSize: 15 }}>
        {title}
      </h2>
      <p style={{ margin: '0 0 8px', color: '#4b5563' }}>{note}</p>
      <label>
        Project ID
        <input
          value={projectId}
          onChange={(event) => setProjectId(event.target.value.trim())}
          placeholder="018f0000-…"
        />
      </label>
      {projectId ? (
        render(projectId)
      ) : (
        <p>Nhập project ID để quản lý cấu hình theo project.</p>
      )}
    </section>
  );
}
