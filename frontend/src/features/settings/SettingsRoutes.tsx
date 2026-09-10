import { Navigate, type RouteObject } from 'react-router-dom';

import { Diagnostics } from '../diagnostics/Diagnostics';
import { ModelCatalog } from '../modelCatalog/ModelCatalog';
import { ProfileEditor } from '../providers/ProfileEditor';
import { ProjectScopedPanel } from './ProjectScopedPanel';
import { SettingsLayout } from './SettingsLayout';
import { StyleManager } from './StyleManager';

export interface SettingsGroupPageProps {
  title: string;
  note: string;
}

export function SettingsGroupPending({ title, note }: SettingsGroupPageProps) {
  return (
    <section aria-labelledby="settings-group-heading">
      <h2 id="settings-group-heading" style={{ margin: '0 0 8px', fontSize: 15 }}>
        {title}
      </h2>
      <p style={{ margin: 0, color: '#4b5563' }}>{note}</p>
    </section>
  );
}

function ProvidersSettings() {
  return <ProfileEditor />;
}

function TranslationSettings() {
  return (
    <ProjectScopedPanel
      title="Translation"
      note="Style/ngôn ngữ/thể loại là cấu hình theo project; quality và quote nằm ở màn dịch của chương."
      render={(projectId) => <StyleManager projectId={projectId} />}
    />
  );
}

function TtsSettings() {
  return (
    <SettingsGroupPending
      title="TTS"
      note="Engine/giọng đọc và preview sẽ nối ở A02 (catalog giọng local đã có)."
    />
  );
}

function StorageSettings() {
  return (
    <SettingsGroupPending
      title="Storage"
      note="Dung lượng/backup/restore/retention sẽ nối ở U10 (API /api/storage và CleanupPreview đã có, cần dữ liệu plan)."
    />
  );
}

function AppearanceSettings() {
  return (
    <SettingsGroupPending
      title="Appearance"
      note="Theme/font/mật độ hiển thị sẽ nối ở U10 trên token của U01."
    />
  );
}

export const settingsGroupRoutes: RouteObject[] = [
  {
    path: 'settings',
    element: <SettingsLayout />,
    children: [
      { index: true, element: <Navigate to="providers" replace /> },
      { path: 'providers', element: <ProvidersSettings /> },
      { path: 'models', element: <ModelCatalog /> },
      { path: 'translation', element: <TranslationSettings /> },
      { path: 'tts', element: <TtsSettings /> },
      { path: 'storage', element: <StorageSettings /> },
      { path: 'appearance', element: <AppearanceSettings /> },
      { path: 'advanced', element: <Diagnostics /> },
    ],
  },
];
