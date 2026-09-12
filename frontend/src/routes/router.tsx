import { createBrowserRouter, Link, Navigate, useLocation, useNavigate, useParams } from 'react-router-dom';
import { useEffect, useState } from 'react';
import { Shell } from './Shell';
import { ImportScreen } from './screens/ImportScreen';
import { BatchScreen } from './screens/BatchScreen';
import { TranslationScreen } from './screens/TranslationScreen';
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
import ArtifactPlayer from '../features/audio/ArtifactPlayer';
import VoicePreviewPanel, { type VoiceOption } from '../features/voices/VoicePreviewPanel';
import MultiVoiceCloudDemo from '../features/voices/MultiVoiceCloudDemo';

const fakePresetId = '018f0000-0000-7000-8000-000000000001';
const fakeAudioEnabled = ((import.meta as ImportMeta & { env?: Record<string, string> }).env?.VITE_STUDIO_FAKE_AUDIO) === '1';

type RenderedAudio = {
  masterArtifactId: string;
  masterSha256: string;
  renderedSegmentIds: string[];
  reusedSegmentIds: string[];
};

type AudioStatus = {
  chapterId: string;
  masterArtifactId: string | null;
  masterSha256: string | null;
  approved: boolean;
};

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

type VoiceCatalogPayload = {
  voices: {
    id: string;
    name: string;
    locale: string;
    available: boolean;
    active: boolean;
    activationHint?: string;
  }[];
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

function VoiceScreen() {
  const { chapterId } = useParams();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [presetId, setPresetId] = useState(fakeAudioEnabled ? fakePresetId : '');
  const [voices, setVoices] = useState<VoiceOption[]>([]);

  useEffect(() => {
    if (fakeAudioEnabled) {
      return;
    }
    let cancelled = false;
    apiJson<VoiceCatalogPayload>('/api/voices?locale=vi-VN')
      .then((payload) => {
        if (cancelled) {
          return;
        }
        // Nghe thử chỉ có nghĩa với giọng đã cài model + license; giọng chưa khả dụng
        // vẫn hiện trong danh sách kèm hướng dẫn, nhưng không có nút điều khiển giả (A02).
        setVoices(
          payload.voices.map((voice) => ({
            id: voice.id,
            name: voice.name,
            locale: voice.locale,
            available: voice.available,
            activationHint: voice.activationHint,
          })),
        );
        const selected = payload.voices.find((voice) => voice.active && voice.available)
          ?? payload.voices.find((voice) => voice.available);
        setPresetId(selected?.id ?? '');
      })
      .catch((reason) => {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : 'VOICE_CATALOG_FAILED');
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function renderAudio() {
    if (!chapterId) {
      return;
    }
    setBusy(true);
    setError('');
    try {
      await apiJson(`/api/chapters/${chapterId}/audio/configure-single`, {
        method: 'POST',
        body: { presetId },
      });
      const rendered = await apiJson<RenderedAudio>(`/api/chapters/${chapterId}/audio/render`, {
        method: 'POST',
        body: {},
      });
      navigate(`/chapters/${chapterId}/audio`, { state: { rendered } });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'AUDIO_RENDER_FAILED');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section style={styles.panel} aria-label="Chọn giọng">
      <h1 style={styles.title}>Giọng đọc</h1>
      <p style={styles.quote}>
        {presetId ? `Preset ${presetId}` : 'Chưa có giọng local đã verify để render.'}
      </p>
      <VoicePreviewPanel
        voices={voices}
        selectedVoiceId={presetId || null}
        onSelect={(voiceId) => setPresetId(voiceId)}
      />
      <button type="button" onClick={() => void renderAudio()} disabled={busy || !presetId} style={styles.primaryButton}>
        Render một giọng
      </button>
      {error ? <p role="alert" style={styles.error}>{error}</p> : null}
    </section>
  );
}

type ServerMaster = {
  masterArtifactId: string;
  masterSha256: string;
};

/** Mã lỗi backend báo bản master đang duyệt đã cũ; phải nạp lại trạng thái server (A05). */
const STALE_MASTER_CODES = [
  'MASTER_HASH_MISMATCH',
  'MASTER_ARTIFACT_NOT_READY',
  'MASTER_TRANSLATION_STALE',
  'MASTER_VOICE_PLAN_STALE',
  'MASTER_PROBE_HASH_MISMATCH',
];

function AudioScreen() {
  const { chapterId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const initialRendered = (location.state as { rendered?: RenderedAudio } | null)?.rendered ?? null;
  const [rendered, setRendered] = useState<RenderedAudio | null>(initialRendered);
  const [serverMaster, setServerMaster] = useState<ServerMaster | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  // Bản master trên server là nguồn duy nhất; nếu khác bản đang nghe thì phải nghe lại
  // trước khi phê duyệt (tránh duyệt nhầm bản cũ - A05).
  const masterIsStale =
    rendered !== null &&
    serverMaster !== null &&
    (rendered.masterArtifactId !== serverMaster.masterArtifactId ||
      rendered.masterSha256 !== serverMaster.masterSha256);

  useEffect(() => {
    if (!chapterId) {
      return;
    }
    let cancelled = false;
    apiJson<AudioStatus>(`/api/chapters/${chapterId}/audio/status`)
      .then((status) => {
        if (cancelled || !status.masterArtifactId || !status.masterSha256) {
          return;
        }
        const master = {
          masterArtifactId: status.masterArtifactId,
          masterSha256: status.masterSha256,
        };
        setServerMaster(master);
        // Điều hướng trực tiếp (không có state từ màn render) thì lấy luôn master của server;
        // nếu đã có bản đang nghe thì giữ nguyên và để guard stale xử lý.
        setRendered((current) =>
          current ?? { ...master, renderedSegmentIds: [], reusedSegmentIds: [] },
        );
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [chapterId]);

  function useServerMaster() {
    if (!serverMaster) {
      return;
    }
    setRendered({
      masterArtifactId: serverMaster.masterArtifactId,
      masterSha256: serverMaster.masterSha256,
      renderedSegmentIds: [],
      reusedSegmentIds: [],
    });
    setNotice('Đang nghe bản master mới nhất trên máy chủ.');
    setError('');
  }

  async function reloadServerMaster() {
    if (!chapterId) {
      return;
    }
    try {
      const status = await apiJson<AudioStatus>(`/api/chapters/${chapterId}/audio/status`);
      if (status.masterArtifactId && status.masterSha256) {
        setServerMaster({
          masterArtifactId: status.masterArtifactId,
          masterSha256: status.masterSha256,
        });
      }
    } catch {
      // giữ nguyên trạng thái cũ; người dùng vẫn thấy cảnh báo stale nếu có
    }
  }

  async function approveAudio() {
    if (!chapterId || !rendered || masterIsStale) {
      return;
    }
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await apiJson(`/api/chapters/${chapterId}/audio/approve`, {
        method: 'POST',
        body: {
          masterArtifactId: rendered.masterArtifactId,
          expectedSha256: rendered.masterSha256,
        },
      });
      navigate(`/chapters/${chapterId}/export`);
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : 'AUDIO_APPROVAL_FAILED';
      setError(message);
      if (STALE_MASTER_CODES.some((code) => message.includes(code))) {
        await reloadServerMaster();
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <section style={styles.panel} aria-label="Duyệt audio">
      <h1 style={styles.title}>Audio</h1>
      {rendered ? (
        <>
          <p style={styles.success}>Master {rendered.masterSha256.slice(0, 12)} sẵn sàng duyệt</p>
          <ArtifactPlayer
            chapterId={chapterId ?? ''}
            artifactId={rendered.masterArtifactId}
            sha256={rendered.masterSha256}
          />
          {masterIsStale ? (
            <p role="alert" style={styles.error}>
              Bản master trên máy chủ đã thay đổi ({(serverMaster?.masterSha256 ?? '').slice(0, 12)}). Hãy nghe lại
              bản mới trước khi phê duyệt.
              <button type="button" onClick={useServerMaster} style={styles.secondaryButton}>
                Nghe bản mới
              </button>
            </p>
          ) : null}
          <button
            type="button"
            onClick={() => void approveAudio()}
            disabled={busy || masterIsStale}
            style={styles.primaryButton}
          >
            Phê duyệt audio
          </button>
        </>
      ) : (
        <Link to={`/chapters/${chapterId}/voice`} style={styles.navLink}>Render lại audio</Link>
      )}
      {notice ? <p role="status" style={styles.success}>{notice}</p> : null}
      {error ? <p role="alert" style={styles.error}>{error}</p> : null}
    </section>
  );
}

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
