import { Navigate, type RouteObject } from 'react-router-dom';

import { Diagnostics } from '../diagnostics/Diagnostics';
import ProviderSettings from '../providers/ProviderSettings';
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

function TranslationSettings() {
  return (
    <SettingsGroupPending
      title="Translation"
      note="NgÃ´n ngá»¯, thá»ƒ loáº¡i, style vÃ  quality sáº½ Ä‘Æ°á»£c ná»‘i á»Ÿ U08â€“U09 (style profile Ä‘Ã£ cÃ³ API backend)."
    />
  );
}

function ProvidersSettings() {
  return (
    <SettingsGroupPending
      title="AI Providers"
      note="Credential/tráº¡ng thÃ¡i provider sáº½ ná»‘i á»Ÿ U08 (component ProviderSettings Ä‘Ã£ cÃ³ nhÆ°ng cáº§n props profile/model)."
    />
  );
}

function ModelsSettings() {
  return (
    <SettingsGroupPending
      title="Models"
      note="Catalog/filter model sáº½ ná»‘i á»Ÿ U08 (API /api/models Ä‘Ã£ cÃ³)."
    />
  );
}

function TtsSettings() {
  return (
    <SettingsGroupPending
      title="TTS"
      note="Engine/giá»ng Ä‘á»c vÃ  preview sáº½ ná»‘i á»Ÿ A02 (catalog giá»ng local Ä‘Ã£ cÃ³)."
    />
  );
}

function AppearanceSettings() {
  return (
    <SettingsGroupPending
      title="Appearance"
      note="Theme/font/máº­t Ä‘á»™ hiá»ƒn thá»‹ sáº½ ná»‘i á»Ÿ U10 trÃªn token cá»§a U01."
    />
  );
}

function StorageSettings() {
  return (
    <SettingsGroupPending
      title="Storage"
      note="Dung lÆ°á»£ng/backup/restore/retention sáº½ ná»‘i á»Ÿ U10 (API /api/storage vÃ  CleanupPreview Ä‘Ã£ cÃ³, cáº§n dá»¯ liá»‡u plan)."
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
      { path: 'models', element: <ModelsSettings /> },
      { path: 'translation', element: <TranslationSettings /> },
      { path: 'tts', element: <TtsSettings /> },
      { path: 'storage', element: <StorageSettings /> },
      { path: 'appearance', element: <AppearanceSettings /> },
      { path: 'advanced', element: <Diagnostics /> },
    ],
  },
];
