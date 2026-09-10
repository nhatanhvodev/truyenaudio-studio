import { Navigate, NavLink, Outlet, useParams, type RouteObject } from 'react-router-dom';

import { CharacterManager } from '../characters/CharacterManager';
import { Diagnostics } from '../diagnostics/Diagnostics';
import { GlossaryManager } from '../glossary/GlossaryManager';
import { MemoryManager } from '../memory/MemoryManager';
import { SettingsGroupPending } from './SettingsRoutes';
import { StyleManager } from './StyleManager';

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
    <section style={{ display: 'flex', gap: 24, padding: 24, alignItems: 'flex-start' }}>
      <nav aria-label="Nhóm cài đặt dự án" style={{ minWidth: 220 }}>
        <h1 style={{ fontSize: 16, margin: '0 0 4px' }}>Cài đặt dự án</h1>
        <p style={{ margin: '0 0 12px', color: '#4b5563', fontSize: 12 }}>{projectId}</p>
        <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'grid', gap: 4 }}>
          {PROJECT_SETTINGS_GROUPS.map((group) => (
            <li key={group.slug}>
              <NavLink
                to={`/projects/${projectId}/settings/${group.slug}`}
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
        <NavLink to={`/projects/${projectId}/import`} style={{ fontSize: 12 }}>
          ← Về import của dự án
        </NavLink>
      </nav>
      <div style={{ flex: 1, minWidth: 0 }}>
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

export const projectSettingsRoutes: RouteObject[] = [
  {
    path: 'projects/:projectId/settings',
    element: <ProjectSettingsShell />,
    children: [
      { index: true, element: <Navigate to="translation" replace /> },
      { path: 'translation', element: <ProjectTranslationPanel /> },
      {
        path: 'tts',
        element: (
          <SettingsGroupPending
            title="TTS"
            note="Engine/giọng đọc và preview sẽ nối ở A02 (catalog giọng local đã có)."
          />
        ),
      },
      {
        path: 'storage',
        element: (
          <SettingsGroupPending
            title="Storage"
            note="Dung lượng/backup/restore/retention sẽ nối ở U10 (API /api/storage đã có)."
          />
        ),
      },
      {
        path: 'appearance',
        element: (
          <SettingsGroupPending
            title="Appearance"
            note="Theme/font/mật độ hiển thị sẽ nối ở U10 trên token của U01."
          />
        ),
      },
      { path: 'advanced', element: <Diagnostics /> },
    ],
  },
];
