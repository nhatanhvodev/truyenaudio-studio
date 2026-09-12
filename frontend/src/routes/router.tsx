import { createBrowserRouter, Navigate, useNavigate, useParams } from 'react-router-dom';
import { Shell } from './Shell';
import { ImportScreen } from './screens/ImportScreen';
import { BatchScreen } from './screens/BatchScreen';
import { TranslationScreen } from './screens/TranslationScreen';
import { VoiceScreen } from './screens/VoiceScreen';
import { AudioScreen } from './screens/AudioScreen';
import { Diagnostics } from '../features/diagnostics/Diagnostics';
import { settingsGroupRoutes } from '../features/settings/SettingsRoutes';
import { projectSettingsRoutes } from '../features/settings/ProjectSettingsRoutes';
import { QualityPlanPanel } from '../features/providers/QualityPlanPanel';
import { ExportWorkflow } from '../features/exports/ExportWorkflow';
import { JobProgress } from '../features/jobs/JobProgress';
import JobDraftPanel from '../features/jobs/JobDraftPanel';
import JobsList from '../features/jobs/JobsList';
import { defaultJobEventStore, type JobEvent } from '../features/jobs/jobStore';
import BilingualEditor from '../features/translation/BilingualEditor';
import { ProjectWizard } from '../features/projects/ProjectWizard';
import { apiJson } from '../shared/api';
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

function ExportScreen() {
  const { chapterId } = useParams();
  if (!chapterId) {
    return <Navigate to="/" replace />;
  }
  return (
    <section style={styles.panel} aria-label="Xuất bản">
      {/* ExportWorkflow sở hữu tiêu đề "Xuất bản"; không render thêm heading trùng. */}
      <ExportWorkflow chapterId={chapterId} />
    </section>
  );
}

/**
 * U07: cancel/retry actions shared by the jobs list and the batch queue.
 *
 * Both actions are real API calls; the durable snapshot is re-read afterwards on
 * success *and* failure, so the UI never displays a status the backend did not
 * confirm. A refused action (409/404) is therefore not an error dialog: the job
 * simply shows its real state again.
 */
function jobActions(store = defaultJobEventStore) {
  const act = (suffix: 'cancel' | 'retry') => async (event: JobEvent) => {
    try {
      await apiJson(`/api/jobs/${encodeURIComponent(event.jobId)}/${suffix}`, { method: 'POST' });
    } catch {
      // Refused (e.g. JOB_RETRY_NOT_RETRYABLE) — refresh() below reconciles.
    }
    await store.refresh();
  };
  return { onCancel: act('cancel'), onRetry: act('retry') };
}

function JobsScreen() {
  const { onCancel, onRetry } = jobActions();
  return (
    <section style={styles.panel}>
      <h1 style={styles.title}>Jobs</h1>
      <JobsList onRetry={onRetry} onCancel={onCancel} />
      <JobProgress />
    </section>
  );
}

function BilingualScreen() {
  const { chapterId } = useParams();
  const navigate = useNavigate();
  if (!chapterId) {
    return <Navigate to="/" replace />;
  }
  return (
    <section style={styles.panel} aria-label="Editor song ngữ">
      <BilingualEditor
        chapterId={chapterId}
        onApproved={() => navigate(`/chapters/${chapterId}/voice`)}
      />
    </section>
  );
}

function JobDraftScreen() {  const { jobId } = useParams();
  if (!jobId) {
    return <Navigate to="/jobs" replace />;
  }
  return (
    <section style={styles.panel} aria-label="Nháp job">
      <h1 style={styles.title}>Nháp đang dịch</h1>
      <JobDraftPanel jobId={jobId} />
    </section>
  );
}

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
