import { Navigate, NavLink, Outlet, useParams, type RouteObject } from 'react-router-dom';

import { CharacterManager } from '../characters/CharacterManager';
import { Diagnostics } from '../diagnostics/Diagnostics';
import { GlossaryManager } from '../glossary/GlossaryManager';
import { MemoryManager } from '../memory/MemoryManager';
import { StyleManager } from './StyleManager';
import { StorageSettings } from './StorageSettings';
import { AppearanceSettings } from './AppearanceSettings';
import VoiceBrowser from '../voices/VoiceBrowser';

import styles from './ProjectSettingsRoutes.module.css';

export const PROJECT_SETTINGS_GROUPS = [
  { slug: 'translation', label: 'Translation' },
  { slug: 'tts', label: 'TTS' },
  { slug: 'storage', label: 'Storage' },
  { slug: 'appearance', label: 'Appearance' },
  { slug: 'advanced', label: 'Advanced' },
] as const;

export function ProjectSettingsShell() {
  const { projectId } = useParams();
  if (!projectId) {
    return <Navigate to="/" replace />;
  }
  return (
    <section className={styles.groups}>
      <nav aria-label="Nhóm cài đặt dự án" className={styles.groupNav}>
        <h1 className={styles.heading}>Cài đặt dự án</h1>
        <p className={styles.projectId}>{projectId}</p>
        <ul className={styles.groupList}>
          {PROJECT_SETTINGS_GROUPS.map((group) => (
            <li key={group.slug}>
              <NavLink to={`/projects/${projectId}/settings/${group.slug}`} className={styles.groupLink}>
                {group.label}
              </NavLink>
            </li>
          ))}
        </ul>
        <NavLink to={`/projects/${projectId}/import`} className={styles.crumb}>
          ← Về import của dự án
        </NavLink>
      </nav>
      <div className={styles.groupBody}>
        <Outlet />
      </div>
    </section>
  );
}

function ProjectTranslationPanel() {
  const { projectId } = useParams();
  if (!projectId) {
    return <Navigate to="/" replace />;
  }
  return (
    <>
      <StyleManager projectId={projectId} />
      <GlossaryManager projectId={projectId} />
      <CharacterManager projectId={projectId} />
      <MemoryManager projectId={projectId} />
    </>
  );
}

function ProjectTtsPanel() {
  return (
    <section aria-label="TTS dự án" className={styles.panel}>
      <h2 className={styles.panelTitle}>TTS</h2>
      <p className={styles.panelNote}>
        Catalog giọng đọc cục bộ. Giọng chỉ khả dụng khi model + license đã được cài trên máy; nếu chưa, danh
        sách hiển thị trạng thái chưa khả dụng và không gọi mạng.
      </p>
      <VoiceBrowser />
    </section>
  );
}

export const projectSettingsRoutes: RouteObject[] = [
  {
    path: 'projects/:projectId/settings',
    element: <ProjectSettingsShell />,
    children: [
      { index: true, element: <Navigate to="translation" replace /> },
      { path: 'translation', element: <ProjectTranslationPanel /> },
      { path: 'tts', element: <ProjectTtsPanel /> },
      { path: 'storage', element: <StorageSettings /> },
      { path: 'appearance', element: <AppearanceSettings /> },
      { path: 'advanced', element: <Diagnostics /> },
    ],
  },
];
