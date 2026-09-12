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


type GateDecision = {
  allowed: boolean;
  reasons: string[];
  rightsEvaluationHash: string;
};

type ExportBundle = {
  id: string;
  files: string[];
  manifestSha256: string;
  directoryPath: string;
};

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

const styles: Record<string, React.CSSProperties> = {
  shell: {
    minHeight: '100vh',
    boxSizing: 'border-box',
    padding: 24,
    paddingBottom: 150,
    color: '#17202a',
    background: '#f6f8fb',
    fontFamily: 'Inter, Segoe UI, Arial, sans-serif',
  },
  nav: {
    display: 'flex',
    // Wrap so the workflow links reflow at 320/390px and at 200% text size
    // instead of forcing a horizontal scrollbar (G-UX responsive).
    flexWrap: 'wrap',
    gap: 12,
    maxWidth: 920,
    margin: '0 auto 16px',
  },
  navLink: {
    color: '#0b5cad',
    fontWeight: 900,
    textDecoration: 'none',
  },
  panel: {
    display: 'grid',
    gap: 16,
    maxWidth: 920,
    margin: '0 auto',
    padding: 20,
    border: '1px solid #d7dde8',
    borderRadius: 8,
    background: '#ffffff',
  },
  title: {
    margin: 0,
    fontSize: 26,
    letterSpacing: 0,
  },
  label: {
    display: 'grid',
    gap: 8,
    fontWeight: 900,
  },
  textarea: {
    width: '100%',
    minHeight: 220,
    boxSizing: 'border-box',
    padding: 12,
    border: '1px solid #c9d3df',
    borderRadius: 6,
    font: 'inherit',
    lineHeight: 1.5,
  },
  input: {
    width: '100%',
    minHeight: 40,
    boxSizing: 'border-box',
    padding: '8px 10px',
    border: '1px solid #c9d3df',
    borderRadius: 6,
    font: 'inherit',
  },
  primaryButton: {
    justifySelf: 'start',
    padding: '10px 14px',
    border: 0,
    borderRadius: 6,
    background: '#155eef',
    color: '#ffffff',
    fontWeight: 900,
  },
  secondaryButton: {
    justifySelf: 'start',
    padding: '10px 14px',
    border: '1px solid #a9b7c6',
    borderRadius: 6,
    background: '#ffffff',
    color: '#17324d',
    fontWeight: 900,
  },
  actions: {
    display: 'flex',
    gap: 10,
    flexWrap: 'wrap',
  },
  quote: {
    margin: 0,
    color: '#475467',
    fontWeight: 700,
  },
  guardBox: {
    display: 'grid',
    gap: 12,
    padding: 12,
    border: '1px solid #d7dde8',
    borderRadius: 8,
    background: '#f8fafc',
  },
  reviewBox: {
    display: 'grid',
    gap: 12,
  },
  segment: {
    display: 'grid',
    gap: 8,
    padding: 12,
    border: '1px solid #d7dde8',
    borderRadius: 8,
    background: '#f8fafc',
  },
  success: {
    margin: 0,
    color: '#166534',
    fontWeight: 900,
  },
  error: {
    margin: 0,
    color: '#9a3412',
    fontWeight: 900,
  },
  result: {
    display: 'grid',
    gap: 8,
    padding: 12,
    border: '1px solid #c7ead2',
    borderRadius: 8,
    background: '#f0fdf4',
  },
};
