import { createBrowserRouter, Navigate } from 'react-router-dom';
import { Shell } from './Shell';
import { ImportScreen } from './screens/ImportScreen';
import { BatchScreen } from './screens/BatchScreen';
import { TranslationScreen } from './screens/TranslationScreen';
import { VoiceScreen } from './screens/VoiceScreen';
import { AudioScreen } from './screens/AudioScreen';
import { ExportScreen } from './screens/ExportScreen';
import { JobsScreen } from './screens/JobsScreen';
import { BilingualScreen } from './screens/BilingualScreen';
import { JobDraftScreen } from './screens/JobDraftScreen';
import { Diagnostics } from '../features/diagnostics/Diagnostics';
import { settingsGroupRoutes } from '../features/settings/SettingsRoutes';
import { projectSettingsRoutes } from '../features/settings/ProjectSettingsRoutes';
import { ProjectWizard } from '../features/projects/ProjectWizard';
import MultiVoiceCloudDemo from '../features/voices/MultiVoiceCloudDemo';


export const router = createBrowserRouter([
  {
    path: '/',
    element: <Shell />,
    children: [
      { index: true, element: <ProjectWizard /> },
      { path: 'projects/new', element: <ProjectWizard /> },
      { path: 'projects/:projectId/import', element: <ImportScreen /> },
      { path: 'projects/:projectId/batch', element: <BatchScreen /> },
      { path: 'chapters/:chapterId/translation', element: <TranslationScreen /> },
      { path: 'chapters/:chapterId/editor', element: <BilingualScreen /> },
      { path: 'chapters/:chapterId/voice', element: <VoiceScreen /> },
      { path: 'chapters/:chapterId/audio', element: <AudioScreen /> },
      { path: 'chapters/:chapterId/export', element: <ExportScreen /> },
      { path: 'jobs', element: <JobsScreen /> },
      { path: 'jobs/:jobId/draft', element: <JobDraftScreen /> },
      { path: 'diagnostics', element: <Diagnostics /> },
      ...settingsGroupRoutes,
      ...projectSettingsRoutes,
      {
        path: 'multivoice-cloud-demo',
        element: <MultiVoiceCloudDemo />,
      },
      { path: '*', element: <Navigate to="/" replace /> },
    ],
  },
]);
