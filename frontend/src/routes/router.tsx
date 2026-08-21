import { createBrowserRouter, Link, Navigate, Outlet, useLocation, useNavigate, useParams } from 'react-router-dom';
import { useEffect, useState } from 'react';
import { ExportGate } from '../features/exports/ExportGate';
import { JobProgress } from '../features/jobs/JobProgress';
import { ProjectWizard } from '../features/projects/ProjectWizard';
import { apiJson } from '../shared/api';

const fakePresetId = '018f0000-0000-7000-8000-000000000001';

type Chapter = {
  id: string;
  project_id?: string;
  projectId?: string;
  ordinal: number;
  source_title?: string | null;
  sourceTitle?: string | null;
};

type TranslationPayload = {
  run: {
    id: string;
    status: string;
    sha256: string;
  };
  segments: {
    sourceSegmentId: string;
    sourceText: string;
    targetText: string;
  }[];
};

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

export const router = createBrowserRouter([
  {
    path: '/',
    element: <Shell />,
    children: [
      { index: true, element: <ProjectWizard /> },
      { path: 'projects/new', element: <ProjectWizard /> },
      { path: 'projects/:projectId/import', element: <ImportScreen /> },
      { path: 'chapters/:chapterId/translation', element: <TranslationScreen /> },
      { path: 'chapters/:chapterId/voice', element: <VoiceScreen /> },
      { path: 'chapters/:chapterId/audio', element: <AudioScreen /> },
      { path: 'chapters/:chapterId/export', element: <ExportScreen /> },
      { path: 'jobs', element: <JobsScreen /> },
      { path: '*', element: <Navigate to="/" replace /> },
    ],
  },
]);

function Shell() {
  return (
    <main style={styles.shell}>
      <nav style={styles.nav} aria-label="Workflow">
        <Link to="/projects/new" style={styles.navLink}>Dự án</Link>
        <Link to="/jobs" style={styles.navLink}>Jobs</Link>
      </nav>
      <Outlet />
      <JobProgress />
    </main>
  );
}

function ImportScreen() {
  const { projectId } = useParams();
  const navigate = useNavigate();
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function importSource() {
    if (!projectId) {
      return;
    }
    setBusy(true);
    setError('');
    try {
      const payload = await apiJson<{ chapters: Chapter[] }>(`/api/projects/${projectId}/chapters/import`, {
        method: 'POST',
        body: {
          kind: 'PASTE',
          items: [{ ordinal: 1, title: 'Chương 1', text }],
        },
      });
      navigate(`/chapters/${payload.chapters[0].id}/translation`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'IMPORT_FAILED');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section style={styles.panel} aria-label="Nhập nội dung">
      <h1 style={styles.title}>Nhập nội dung</h1>
      <label style={styles.label}>
        Văn bản Trung
        <textarea value={text} onChange={(event) => setText(event.target.value)} style={styles.textarea} />
      </label>
      <button type="button" onClick={() => void importSource()} disabled={busy || !text.trim()} style={styles.primaryButton}>
        Xác nhận snapshot
      </button>
      {error ? <p role="alert" style={styles.error}>{error}</p> : null}
    </section>
  );
}

function TranslationScreen() {
  const { chapterId } = useParams();
  const navigate = useNavigate();
  const [data, setData] = useState<TranslationPayload | null>(null);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!chapterId) {
      return;
    }
    let cancelled = false;
    apiJson<TranslationPayload>(`/api/chapters/${chapterId}/translation`)
      .then((payload) => {
        if (!cancelled) {
          setData(payload);
          setMessage('Chờ duyệt bản dịch');
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [chapterId]);

  async function translate() {
    if (!chapterId) {
      return;
    }
    setBusy(true);
    setError('');
    try {
      const payload = await apiJson<TranslationPayload>(`/api/chapters/${chapterId}/translation/fake`, {
        method: 'POST',
        body: {},
      });
      setData(payload);
      setMessage('Chờ duyệt bản dịch');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'TRANSLATION_FAILED');
    } finally {
      setBusy(false);
    }
  }

  async function approve() {
    if (!chapterId || !data) {
      return;
    }
    setBusy(true);
    setError('');
    try {
      await apiJson(`/api/chapters/${chapterId}/translation/approve`, {
        method: 'POST',
        body: { runId: data.run.id, expectedRunHash: data.run.sha256 },
      });
      navigate(`/chapters/${chapterId}/voice`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'TRANSLATION_APPROVAL_FAILED');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section style={styles.panel} aria-label="Dịch và duyệt">
      <h1 style={styles.title}>Dịch</h1>
      <p style={styles.quote}>Quote fake local: 0 VND, không gọi mạng trả phí.</p>
      <button type="button" onClick={() => void translate()} disabled={busy} style={styles.primaryButton}>
        Dịch bằng fake
      </button>
      {message ? <p role="status" style={styles.success}>{message}</p> : null}
      {data ? (
        <div style={styles.reviewBox}>
          <p>Revision {data.run.sha256.slice(0, 12)}</p>
          {data.segments.map((segment) => (
            <article key={segment.sourceSegmentId} style={styles.segment}>
              <strong>{segment.sourceText}</strong>
              <span>{segment.targetText}</span>
            </article>
          ))}
          <button type="button" onClick={() => void approve()} disabled={busy} style={styles.primaryButton}>
            Phê duyệt bản dịch
          </button>
        </div>
      ) : null}
      {error ? <p role="alert" style={styles.error}>{error}</p> : null}
    </section>
  );
}

function VoiceScreen() {
  const { chapterId } = useParams();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function renderAudio() {
    if (!chapterId) {
      return;
    }
    setBusy(true);
    setError('');
    try {
      await apiJson(`/api/chapters/${chapterId}/audio/configure-single`, {
        method: 'POST',
        body: { presetId: fakePresetId },
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
      <p style={styles.quote}>Fake offline narrator, 44.1 kHz, single narrator only.</p>
      <button type="button" onClick={() => void renderAudio()} disabled={busy} style={styles.primaryButton}>
        Render một giọng
      </button>
      {error ? <p role="alert" style={styles.error}>{error}</p> : null}
    </section>
  );
}

function AudioScreen() {
  const { chapterId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const initialRendered = (location.state as { rendered?: RenderedAudio } | null)?.rendered ?? null;
  const [rendered, setRendered] = useState<RenderedAudio | null>(initialRendered);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

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
        setRendered({
          masterArtifactId: status.masterArtifactId,
          masterSha256: status.masterSha256,
          renderedSegmentIds: [],
          reusedSegmentIds: [],
        });
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [chapterId]);

  async function approveAudio() {
    if (!chapterId || !rendered) {
      return;
    }
    setBusy(true);
    setError('');
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
      setError(reason instanceof Error ? reason.message : 'AUDIO_APPROVAL_FAILED');
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
          <button type="button" onClick={() => void approveAudio()} disabled={busy} style={styles.primaryButton}>
            Phê duyệt audio
          </button>
        </>
      ) : (
        <Link to={`/chapters/${chapterId}/voice`} style={styles.navLink}>Render lại audio</Link>
      )}
      {error ? <p role="alert" style={styles.error}>{error}</p> : null}
    </section>
  );
}

function ExportScreen() {
  const { chapterId } = useParams();
  const [gate, setGate] = useState<GateDecision | null>(null);
  const [bundle, setBundle] = useState<ExportBundle | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!chapterId) {
      return;
    }
    apiJson<GateDecision>(`/api/chapters/${chapterId}/exports/gate`)
      .then(setGate)
      .catch((reason) => setError(reason instanceof Error ? reason.message : 'EXPORT_GATE_FAILED'));
  }, [chapterId]);

  async function buildPublication() {
    if (!chapterId) {
      return;
    }
    setError('');
    try {
      const payload = await apiJson<ExportBundle>(`/api/chapters/${chapterId}/exports/publication`, {
        method: 'POST',
        body: {
          episodeTitle: 'Tập 1',
          suggestedEpisodeNumber: 1,
          isPremium: false,
        },
      });
      setBundle(payload);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'EXPORT_FAILED');
    }
  }

  return (
    <section style={styles.panel} aria-label="Xuất bản">
      <h1 style={styles.title}>Export</h1>
      {gate ? <ExportGate decision={gate} onBuildPublication={() => void buildPublication()} /> : <p>Đang kiểm tra quyền</p>}
      {bundle ? (
        <section style={styles.result}>
          <strong>Đã verify checksum</strong>
          <span>{bundle.manifestSha256.slice(0, 12)}</span>
          <span>{bundle.files.join(', ')}</span>
        </section>
      ) : null}
      {error ? <p role="alert" style={styles.error}>{error}</p> : null}
    </section>
  );
}

function JobsScreen() {
  return (
    <section style={styles.panel}>
      <h1 style={styles.title}>Jobs</h1>
      <JobProgress />
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
  primaryButton: {
    justifySelf: 'start',
    padding: '10px 14px',
    border: 0,
    borderRadius: 6,
    background: '#155eef',
    color: '#ffffff',
    fontWeight: 900,
  },
  quote: {
    margin: 0,
    color: '#475467',
    fontWeight: 700,
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
