import { createBrowserRouter, Link, Navigate, Outlet, useLocation, useNavigate, useParams } from 'react-router-dom';
import { useEffect, useState } from 'react';
import { BatchQueue } from '../features/batch/BatchQueue';
import { ExportGate } from '../features/exports/ExportGate';
import { JobProgress } from '../features/jobs/JobProgress';
import { ProjectWizard } from '../features/projects/ProjectWizard';
import { apiJson } from '../shared/api';

const fakePresetId = '018f0000-0000-7000-8000-000000000001';
const fakeAudioEnabled = ((import.meta as ImportMeta & { env?: Record<string, string> }).env?.VITE_STUDIO_FAKE_AUDIO) === '1';

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

type VoiceCatalogPayload = {
  voices: {
    id: string;
    available: boolean;
    active: boolean;
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

function BatchScreen() {
  const { projectId } = useParams();
  if (!projectId) {
    return <Navigate to="/" replace />;
  }
  return <BatchQueue projectId={projectId} />;
}

function TranslationScreen() {
  const { chapterId } = useParams();
  const navigate = useNavigate();
  const [data, setData] = useState<TranslationPayload | null>(null);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [cloudConsentId, setCloudConsentId] = useState('');
  const [budgetAuthorizationId, setBudgetAuthorizationId] = useState('');

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

  async function translateFake() {
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

  async function translateQwen() {
    if (!chapterId) {
      return;
    }
    if (!cloudConsentId.trim() || !budgetAuthorizationId.trim()) {
      setError('CLOUD_CONSENT_AND_BUDGET_REQUIRED');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const payload = await apiJson<TranslationPayload>(`/api/chapters/${chapterId}/translation/qwen`, {
        method: 'POST',
        body: {
          cloudConsentId: cloudConsentId.trim(),
          budgetAuthorizationId: budgetAuthorizationId.trim(),
        },
      });
      setData(payload);
      setMessage('Chờ duyệt bản dịch');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'QWEN_TRANSLATION_FAILED');
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
      <section style={styles.guardBox} aria-label="Qwen translation">
        <p style={styles.quote}>Qwen cần consent cloud và budget authorization đã tạo trước.</p>
        <label style={styles.label}>
          Cloud consent ID
          <input
            value={cloudConsentId}
            onChange={(event) => setCloudConsentId(event.target.value)}
            style={styles.input}
            autoComplete="off"
          />
        </label>
        <label style={styles.label}>
          Budget authorization ID
          <input
            value={budgetAuthorizationId}
            onChange={(event) => setBudgetAuthorizationId(event.target.value)}
            style={styles.input}
            autoComplete="off"
          />
        </label>
        <button type="button" onClick={() => void translateQwen()} disabled={busy} style={styles.primaryButton}>
          Dịch bằng Qwen
        </button>
      </section>
      <section style={styles.guardBox} aria-label="Fake test translation">
        <p style={styles.quote}>Fake test local: 0 VND, không gọi mạng trả phí.</p>
        <button type="button" onClick={() => void translateFake()} disabled={busy} style={styles.secondaryButton}>
          Dịch bằng fake
        </button>
      </section>
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
  const [presetId, setPresetId] = useState(fakeAudioEnabled ? fakePresetId : '');

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
      <button type="button" onClick={() => void renderAudio()} disabled={busy || !presetId} style={styles.primaryButton}>
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

  async function buildPrivate() {
    if (!chapterId) {
      return;
    }
    setError('');
    try {
      const payload = await apiJson<ExportBundle>(`/api/chapters/${chapterId}/exports/private`, {
        method: 'POST',
        body: {},
      });
      setBundle(payload);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'PRIVATE_EXPORT_FAILED');
    }
  }

  return (
    <section style={styles.panel} aria-label="Xuất bản">
      <h1 style={styles.title}>Export</h1>
      {gate ? (
        <ExportGate
          decision={gate}
          onBuildPrivate={() => void buildPrivate()}
          onBuildPublication={() => void buildPublication()}
        />
      ) : (
        <p>Đang kiểm tra quyền</p>
      )}
      {bundle ? (
        <section style={styles.result}>
          <strong>{bundle.files.includes('PRIVATE_ONLY.txt') ? 'Đã tạo archive riêng tư' : 'Đã verify checksum'}</strong>
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
