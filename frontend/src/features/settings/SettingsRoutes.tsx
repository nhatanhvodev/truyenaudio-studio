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

import styles from './SettingsRoutes.module.css';

export interface SettingsGroupPageProps {
  title: string;
  note: string;
}

export function SettingsGroupPending({ title, note }: SettingsGroupPageProps) {
  return (
    <section aria-labelledby="settings-group-heading" className={styles.panel}>
      <h2 id="settings-group-heading" className={styles.panelHeading}>
        {title}
      </h2>
      <p className={styles.panelNote}>{note}</p>
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
    <section aria-label="TTS" className={styles.panel}>
      <h2 className={styles.panelHeading}>TTS</h2>
      <p className={styles.panelNote}>
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
