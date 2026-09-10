import { Navigate, type RouteObject } from 'react-router-dom';

import { Diagnostics } from '../diagnostics/Diagnostics';
import { CharacterManager } from '../characters/CharacterManager';
import { GlossaryManager } from '../glossary/GlossaryManager';
import { MemoryManager } from '../memory/MemoryManager';
import { ModelCatalog } from '../modelCatalog/ModelCatalog';
import { ProfileEditor } from '../providers/ProfileEditor';
import { ProjectScopedPanel } from './ProjectScopedPanel';
import { SettingsLayout } from './SettingsLayout';
import { StyleManager } from './StyleManager';
import VoiceBrowser from '../voices/VoiceBrowser';
import { AppearanceSettings } from './AppearanceSettings';
import { StorageSettings } from './StorageSettings';

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
      note="Style/glossary là cấu hình theo project; quality và quote nằm ở màn dịch của chương."
      render={(projectId) => (
        <>
          <StyleManager projectId={projectId} />
          <GlossaryManager projectId={projectId} />
          <CharacterManager projectId={projectId} />
          <MemoryManager projectId={projectId} />
        </>
      )}
    />
  );
}

function TtsSettings() {
  return (
    <section aria-label="TTS" style={{ display: 'grid', gap: 12 }}>
      <h2 style={{ margin: 0, fontSize: 15 }}>TTS</h2>
      <p style={{ margin: 0, color: '#4b5563', fontSize: 13 }}>
        Catalog giọng đọc cục bộ (VieNeu). Giọng chỉ khả dụng khi đã cài model + license trên máy; nếu chưa,
        catalog hiển thị trạng thái “chưa khả dụng” và không thể preview — không có lời gọi mạng nào được thực hiện.
      </p>
      <VoiceBrowser />
    </section>
  );
}

function StorageSettingsPage() {
  return <StorageSettings />;
}

function AppearanceSettingsPage() {
  return <AppearanceSettings />;
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
      { path: 'storage', element: <StorageSettingsPage /> },
      { path: 'appearance', element: <AppearanceSettingsPage /> },
      { path: 'advanced', element: <Diagnostics /> },
    ],
  },
];
