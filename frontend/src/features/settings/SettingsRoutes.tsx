import { Navigate, type RouteObject } from 'react-router-dom';

import { Diagnostics } from '../diagnostics/Diagnostics';
import { ModelCatalog } from '../modelCatalog/ModelCatalog';
import { SettingsLayout } from './SettingsLayout';

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
  return (
    <SettingsGroupPending
      title="AI Providers"
      note="Credential và trạng thái provider sẽ nối ở U08 (component ProviderSettings cần props profile/model)."
    />
  );
}

function TranslationSettings() {
  return (
    <SettingsGroupPending
      title="Translation"
      note="Ngôn ngữ, thể loại, style và quality sẽ nối ở U08–U09 (style profile đã có API backend)."
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
