import { createBrowserRouter, Link, Navigate, Outlet, useLocation, useNavigate, useParams } from 'react-router-dom';
import { useEffect, useState } from 'react';
import { BatchQueue } from '../features/batch/BatchQueue';
import { Diagnostics } from '../features/diagnostics/Diagnostics';
import { settingsGroupRoutes } from '../features/settings/SettingsRoutes';
import { projectSettingsRoutes } from '../features/settings/ProjectSettingsRoutes';
import { GlobalNav } from '../features/workspace/GlobalNav';
import { WorkspaceTabs } from '../features/workspace/WorkspaceTabs';
import { describeRoute, projectIdForRoute } from '../features/workspace/workspaceRoutes';
import { ExportGate } from '../features/exports/ExportGate';
import { JobProgress } from '../features/jobs/JobProgress';
import JobDraftPanel from '../features/jobs/JobDraftPanel';
import JobsList from '../features/jobs/JobsList';
import BilingualEditor from '../features/translation/BilingualEditor';
import { ProjectWizard } from '../features/projects/ProjectWizard';
import { WenkuImport } from '../features/import/WenkuImport';
import { WenkuCrawlProvider } from '../features/import/WenkuCrawlContext';
import { apiForm, apiJson } from '../shared/api';
import ImportPreview, { type ImportCandidate } from '../features/import/ImportPreview';
import MultiVoiceCloudDemo from '../features/voices/MultiVoiceCloudDemo';

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

type TranslationIssue = {
  id: string;
  category: string;
  severity: string;
  status: string;
  evidence?: string | null;
  suggestion?: string | null;
  sourceSegmentId?: string | null;
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
  issues: TranslationIssue[];
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

function Shell() {
  const location = useLocation();
  const navigate = useNavigate();
  const [visited, setVisited] = useState<string[]>([location.pathname]);

  useEffect(() => {
    setVisited((current) =>
      current.includes(location.pathname) ? current : [...current, location.pathname],
    );
  }, [location.pathname]);

  const openTabs = visited.map(describeRoute);
  const currentTabId = openTabs.some((tab) => tab.id === location.pathname) ? location.pathname : null;
  const layoutProjectId = projectIdForRoute(location.pathname) ?? 'local';

  return (
    <WenkuCrawlProvider>
      <main style={styles.shell}>
        <GlobalNav />
        <nav style={styles.nav} aria-label="Workflow">
          <Link to="/" style={styles.navLink}>Dự án</Link>
          <Link to="/jobs" style={styles.navLink}>Jobs</Link>
          <Link to="/diagnostics" style={styles.navLink}>Diagnostics</Link>
        </nav>
        <WorkspaceTabs
          projectId={layoutProjectId}
          openTabs={openTabs}
          currentTabId={currentTabId}
          onNavigate={(to) => navigate(to)}
        />
        <Outlet />
        <JobProgress />
      </main>
    </WenkuCrawlProvider>
  );
}

function ImportScreen() {
  const { projectId } = useParams();
  const navigate = useNavigate();
  const [folderPath, setFolderPath] = useState('');
  const [bookFile, setBookFile] = useState<File | null>(null);
  const [candidates, setCandidates] = useState<ImportCandidate[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  if (!projectId) {
    return <Navigate to="/" replace />;
  }

  async function previewFolder() {
    if (!projectId || !folderPath.trim()) {
      return;
    }
    const form = new FormData();
    form.set('kind', 'LOCAL_FOLDER');
    form.set('localFolderPath', folderPath.trim());
    await preview(form);
  }

  async function previewBook(kind: 'EPUB' | 'DOCX') {
    if (!bookFile || !projectId) {
      return;
    }
    const form = new FormData();
    form.set('kind', kind);
    form.set('file', bookFile);
    await preview(form);
  }

  async function preview(form: FormData) {
    if (!projectId) {
      return;
    }
    setBusy(true);
    setError('');
    try {
      const payload = await apiForm<{ candidates: ImportCandidate[] }>(
        `/api/projects/${projectId}/chapters/import/preview`,
        form,
      );
      setCandidates(payload.candidates);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'IMPORT_PREVIEW_FAILED');
    } finally {
      setBusy(false);
    }
  }

  async function confirmPreview(nextCandidates: ImportCandidate[]) {
    if (!projectId) {
      return;
    }
    const items = nextCandidates.map((candidate) => ({
      ordinal: candidate.ordinal,
      title: candidate.title ?? `Chuong ${candidate.ordinal}`,
      text: candidate.text,
    }));
    if (items.some((item) => item.ordinal === null || item.ordinal <= 0 || !item.text.trim())) {
      setError('IMPORT_MAPPING_REQUIRED');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const payload = await apiJson<{ chapters: Chapter[] }>(`/api/projects/${projectId}/chapters/import`, {
        method: 'POST',
        body: {
          kind: 'PASTE',
          items,
        },
      });
      navigate(`/chapters/${payload.chapters[0].id}/translation`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'IMPORT_CONFIRM_FAILED');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section style={styles.panel} aria-label="Nhập nội dung">
      <h1 style={styles.title}>Nhập nội dung</h1>
      <WenkuImport
        projectId={projectId}
        onImportSuccess={(firstChapterId) => {
          navigate(`/chapters/${firstChapterId}/translation`);
        }}
      />

      <div style={{ marginTop: 24, borderTop: '1px solid #e2e8f0', paddingTop: 20 }}>
        <h2 style={{ margin: '0 0 12px', fontSize: 18, color: '#334155' }}>Hoặc nhập từ File / Thư mục máy tính</h2>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 16 }}>
          <section style={styles.guardBox} aria-label="Folder import preview">
            <label style={styles.label}>
              Local folder path
              <input value={folderPath} onChange={(event) => setFolderPath(event.target.value)} style={styles.input} />
            </label>
            <button type="button" onClick={() => void previewFolder()} disabled={busy || !folderPath.trim()} style={styles.secondaryButton}>
              Preview folder
            </button>
          </section>

          <section style={styles.guardBox} aria-label="Book import preview">
            <label style={styles.label}>
              EPUB or DOCX file
              <input
                type="file"
                accept=".epub,.docx,application/epub+zip,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                onChange={(event) => setBookFile(event.target.files?.[0] ?? null)}
                style={styles.input}
              />
            </label>
            <div style={styles.actions}>
              <button type="button" onClick={() => void previewBook('EPUB')} disabled={busy || !bookFile} style={styles.secondaryButton}>
                Preview EPUB
              </button>
              <button type="button" onClick={() => void previewBook('DOCX')} disabled={busy || !bookFile} style={styles.secondaryButton}>
                Preview DOCX
              </button>
            </div>
          </section>
        </div>

        {candidates.length > 0 ? <ImportPreview candidates={candidates} onConfirm={(mapped) => void confirmPreview(mapped)} /> : null}
        {error ? <p role="alert" style={styles.error}>{error}</p> : null}
      </div>
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
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [editingId, setEditingId] = useState<string | null>(null);
  const [savingId, setSavingId] = useState<string | null>(null);
  const [filter, setFilter] = useState<'ALL' | 'ISSUES_ONLY' | 'BLOCKERS_ONLY'>('ALL');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [geminiApiKey, setGeminiApiKey] = useState('');
  const [geminiProfileId, setGeminiProfileId] = useState('');
  const [qwenProfileId, setQwenProfileId] = useState('');
  const [showKey, setShowKey] = useState(false);
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
          setDrafts(Object.fromEntries(payload.segments.map((s) => [s.sourceSegmentId, s.targetText])));
          setMessage('Đã tải bản dịch hiện tại');
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [chapterId]);

  async function translateGemini() {
    if (!chapterId) {
      return;
    }
    const profileId = geminiProfileId.trim();
    if (!profileId || !cloudConsentId.trim() || !budgetAuthorizationId.trim()) {
      setError('GEMINI_PROFILE_CONSENT_AND_BUDGET_REQUIRED');
      return;
    }
    setBusy(true);
    setError('');
    setMessage('Đang dịch chương truyện bằng Google Gemini... Vui lòng chờ vài giây.');
    try {
      const payload = await apiJson<TranslationPayload>(`/api/chapters/${chapterId}/translation/gemini`, {
        method: 'POST',
        body: {
          profileId,
          cloudConsentId: cloudConsentId.trim(),
          budgetAuthorizationId: budgetAuthorizationId.trim(),
        },
      });
      setData(payload);
      setDrafts(Object.fromEntries(payload.segments.map((s) => [s.sourceSegmentId, s.targetText])));
      setMessage('Đã dịch xong toàn bộ chương bằng Google Gemini! Văn phong tiểu thuyết tự nhiên, mượt mà.');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'GEMINI_TRANSLATION_FAILED');
    } finally {
      setBusy(false);
    }
  }

  async function provisionGeminiCredential() {
    const profileId = geminiProfileId.trim();
    const secret = geminiApiKey.trim();
    if (!profileId || !secret) {
      setError('GEMINI_PROFILE_AND_CREDENTIAL_REQUIRED');
      return;
    }
    setBusy(true);
    setError('');
    try {
      await apiJson(`/api/cloud-profiles/${profileId}/credential`, {
        method: 'PUT',
        body: { secret },
      });
      setMessage('Đã lưu credential vào keyring cục bộ và xóa key khỏi biểu mẫu.');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'CREDENTIAL_PROVISIONING_FAILED');
    } finally {
      setGeminiApiKey('');
      setBusy(false);
    }
  }

  async function translateFake() {
    if (!chapterId) {
      return;
    }
    setBusy(true);
    setError('');
    setMessage('Đang dịch nhanh bằng bộ dịch Convert / Fake nội bộ...');
    try {
      const payload = await apiJson<TranslationPayload>(`/api/chapters/${chapterId}/translation/fake`, {
        method: 'POST',
        body: {},
      });
      setData(payload);
      setDrafts(Object.fromEntries(payload.segments.map((s) => [s.sourceSegmentId, s.targetText])));
      setMessage('Đã dịch hoàn tất bằng bộ dịch Convert / Fake nội bộ (Hán-Việt)!');
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
    if (!qwenProfileId.trim() || !cloudConsentId.trim() || !budgetAuthorizationId.trim()) {
      setError('CLOUD_CONSENT_AND_BUDGET_REQUIRED');
      return;
    }
    setBusy(true);
    setError('');
    setMessage('Đang gửi bản dịch qua Qwen AI...');
    try {
      const payload = await apiJson<TranslationPayload>(`/api/chapters/${chapterId}/translation/qwen`, {
        method: 'POST',
        body: {
          profileId: qwenProfileId.trim(),
          cloudConsentId: cloudConsentId.trim(),
          budgetAuthorizationId: budgetAuthorizationId.trim(),
        },
      });
      setData(payload);
      setDrafts(Object.fromEntries(payload.segments.map((s) => [s.sourceSegmentId, s.targetText])));
      setMessage('Dịch qua Qwen thành công! Vui lòng duyệt bản dịch.');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'QWEN_TRANSLATION_FAILED');
    } finally {
      setBusy(false);
    }
  }

  async function saveSegment(sourceSegmentId: string) {
    if (!chapterId || !data) {
      return;
    }
    setSavingId(sourceSegmentId);
    setError('');
    try {
      const updated = await apiJson<TranslationPayload>(
        `/api/chapters/${chapterId}/translation/segments/${sourceSegmentId}`,
        {
          method: 'PATCH',
          body: {
            runId: data.run.id,
            targetText: drafts[sourceSegmentId] ?? '',
            expectedRunHash: data.run.sha256,
          },
        }
      );
      setData(updated);
      setDrafts(Object.fromEntries(updated.segments.map((s) => [s.sourceSegmentId, s.targetText])));
      setEditingId(null);
      setMessage('Đã cập nhật câu dịch thành công!');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'SAVE_SEGMENT_FAILED');
    } finally {
      setSavingId(null);
    }
  }

  async function approve(force = false) {
    if (!chapterId || !data) {
      return;
    }
    setBusy(true);
    setError('');
    try {
      await apiJson(`/api/chapters/${chapterId}/translation/approve`, {
        method: 'POST',
        body: { runId: data.run.id, expectedRunHash: data.run.sha256, force },
      });
      navigate(`/chapters/${chapterId}/voice`);
    } catch (reason) {
      const msg = reason instanceof Error ? reason.message : 'TRANSLATION_APPROVAL_FAILED';
      if (msg === 'TRANSLATION_QA_BLOCKERS_OPEN') {
        setError('Bản dịch có lỗi QA nghiêm trọng chưa được giải quyết. Bạn có thể sửa câu dịch tương ứng hoặc bấm "Bỏ qua cảnh báo & Duyệt tiếp" bên dưới.');
      } else {
        setError(msg);
      }
    } finally {
      setBusy(false);
    }
  }

  const blockers = (data?.issues ?? []).filter(
    (i) => i.status === 'OPEN' && (i.severity === 'MAJOR' || i.severity === 'CRITICAL')
  );
  const otherIssues = (data?.issues ?? []).filter(
    (i) => !(i.status === 'OPEN' && (i.severity === 'MAJOR' || i.severity === 'CRITICAL'))
  );

  const displayedSegments = (data?.segments ?? []).filter((seg) => {
    if (filter === 'ALL') return true;
    const segIssues = (data?.issues ?? []).filter((i) => i.sourceSegmentId === seg.sourceSegmentId);
    if (filter === 'BLOCKERS_ONLY') {
      return segIssues.some((i) => i.status === 'OPEN' && (i.severity === 'MAJOR' || i.severity === 'CRITICAL'));
    }
    return segIssues.length > 0;
  });

  return (
    <section style={styles.panel} aria-label="Dịch và duyệt">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
        <h1 style={styles.title}>Dịch & Hiệu đính</h1>
        <Link to={`/chapters/${chapterId}/voice`} style={styles.secondaryButton}>
          Chuyển sang Giọng đọc (Voice) →
        </Link>
      </div>

      {/* 🌟 Google AI Studio Gemini Translation - Card Chính */}
      <section
        style={{
          display: 'grid',
          gap: 16,
          padding: 20,
          borderRadius: 10,
          border: '2px solid #818cf8',
          background: 'linear-gradient(135deg, #ffffff 0%, #f5f3ff 100%)',
          boxShadow: '0 4px 16px rgba(99, 102, 241, 0.1)',
        }}
        aria-label="Google AI Studio Gemini translation"
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: 26 }}>✨</span>
            <div>
              <h2 style={{ margin: 0, fontSize: 18, color: '#312e81', fontWeight: 800 }}>
                Dịch bằng Google AI Studio (Gemini Model)
              </h2>
              <p style={{ margin: '2px 0 0', fontSize: 13, color: '#4f46e5' }}>
                Loại bỏ bản dịch test fake · Văn phong tiểu thuyết mượt mà, hỗ trợ thuật ngữ và bộ nhớ truyện
              </p>
            </div>
          </div>
          <a
            href="https://aistudio.google.com/app/apikey"
            target="_blank"
            rel="noreferrer"
            style={{
              fontSize: 13,
              color: '#4338ca',
              fontWeight: 700,
              textDecoration: 'none',
              background: '#e0e7ff',
              padding: '6px 12px',
              borderRadius: 6,
              display: 'inline-flex',
              alignItems: 'center',
              gap: 4,
            }}
          >
            🔑 Lấy API Key miễn phí tại Google AI Studio ↗
          </a>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 14 }}>
          <div>
            <label style={{ display: 'block', fontSize: 13, fontWeight: 700, color: '#374151', marginBottom: 4 }}>
              Google AI Studio API Key
            </label>
            <div style={{ display: 'flex', gap: 6 }}>
              <input
                type={showKey ? 'text' : 'password'}
                value={geminiApiKey}
                onChange={(e) => setGeminiApiKey(e.target.value)}
                placeholder="Dán AIzaSy... từ aistudio.google.com"
                style={{
                  ...styles.input,
                  flex: 1,
                  fontFamily: 'monospace',
                  fontSize: 13,
                  border: geminiApiKey ? '1px solid #818cf8' : '1px solid #f87171',
                }}
                autoComplete="off"
              />
              <button
                type="button"
                onClick={() => setShowKey(!showKey)}
                style={{ ...styles.secondaryButton, padding: '6px 10px', fontSize: 12 }}
                title={showKey ? 'Ẩn key' : 'Hiện key'}
              >
                {showKey ? '🙈' : '👁️'}
              </button>
            </div>
            <div style={{ fontSize: 11, color: '#6b7280', marginTop: 4 }}>
              Credential chỉ tồn tại trong biểu mẫu đến khi gửi vào keyring cục bộ; không lưu trong trình duyệt hoặc dùng từ .env.
            </div>
          </div>

          <div>
            <label style={{ display: 'block', fontSize: 13, fontWeight: 700, color: '#374151', marginBottom: 4 }}>
              Gemini profile ID
            </label>
            <input aria-label="Gemini profile ID" value={geminiProfileId} onChange={(event) => setGeminiProfileId(event.target.value)} placeholder="Profile đã tạo trong AI Providers" style={{ ...styles.input, width: '100%' }} />
            <button type="button" onClick={() => void provisionGeminiCredential()} disabled={busy || !geminiProfileId.trim() || !geminiApiKey.trim()} style={{ ...styles.secondaryButton, marginTop: 6 }}>
              Lưu API key vào profile
            </button>
          </div>

          <p style={{ ...styles.quote, fontSize: 13 }}>
            Model được cấu hình và kiểm soát bởi provider profile cục bộ.
          </p>
        </div>

        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <button
            type="button"
            onClick={() => void translateGemini()}
            disabled={busy || !geminiProfileId.trim() || !cloudConsentId.trim() || !budgetAuthorizationId.trim()}
            style={{
              ...styles.primaryButton,
              background: busy ? '#94a3b8' : 'linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%)',
              fontSize: 15,
              padding: '10px 20px',
              cursor: busy || !geminiProfileId.trim() || !cloudConsentId.trim() || !budgetAuthorizationId.trim() ? 'not-allowed' : 'pointer',
            }}
          >
            {busy ? '⏳ Đang dịch qua Gemini...' : '✨ Dịch toàn bộ chương bằng Gemini AI'}
          </button>
          {!geminiProfileId.trim() ? (
            <span style={{ fontSize: 13, color: '#b91c1c', fontWeight: 600 }}>
              ← Nhập profile Gemini và consent/budget để kích hoạt nút dịch
            </span>
          ) : null}
        </div>
      </section>

      {/* ⚙️ Tùy chọn dịch khác (Collapsible) */}
      <details open style={{ ...styles.guardBox, marginTop: 4, background: '#f8fafc' }}>
        <summary style={{ cursor: 'pointer', fontWeight: 700, color: '#475467', fontSize: 13 }}>
          ⚙️ Tùy chọn dịch khác (Qwen Cloud & Bộ Convert nội bộ)
        </summary>
        <p style={{ ...styles.quote, fontSize: 13, marginTop: 8 }}>
          Qwen cần consent cloud và budget authorization đã tạo trước.
        </p>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 16, marginTop: 12 }}>
          <section style={{ ...styles.guardBox, background: '#ffffff' }} aria-label="Fake test translation">
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span>⚡</span>
              <strong>Dịch nhanh nội bộ (Convert Hán-Việt)</strong>
            </div>
            <p style={{ ...styles.quote, fontSize: 12 }}>Dịch test convert offline, không dùng AI.</p>
            <button type="button" onClick={() => void translateFake()} disabled={busy} style={{ ...styles.secondaryButton, fontSize: 13 }}>
              Dịch convert nội bộ
            </button>
          </section>

          <section style={{ ...styles.guardBox, background: '#ffffff' }} aria-label="Qwen translation">
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span>🤖</span>
              <strong>Dịch qua Qwen AI Cloud</strong>
            </div>
            <label style={styles.label}>
              Qwen profile ID
              <input
                aria-label="Qwen profile ID"
                value={qwenProfileId}
                onChange={(event) => setQwenProfileId(event.target.value)}
                style={styles.input}
                autoComplete="off"
                placeholder="VD: profile-qwen-1"
              />
            </label>
            <label style={styles.label}>
              Cloud consent ID
              <input
                value={cloudConsentId}
                onChange={(event) => setCloudConsentId(event.target.value)}
                style={styles.input}
                autoComplete="off"
                placeholder="VD: consent-123"
              />
            </label>
            <label style={styles.label}>
              Budget authorization ID
              <input
                value={budgetAuthorizationId}
                onChange={(event) => setBudgetAuthorizationId(event.target.value)}
                style={styles.input}
                autoComplete="off"
                placeholder="VD: budget-456"
              />
            </label>
            <button type="button" onClick={() => void translateQwen()} disabled={busy} style={{ ...styles.secondaryButton, fontSize: 13 }}>
              Dịch bằng Qwen
            </button>
          </section>
        </div>
      </details>


      {message ? <p role="status" style={styles.success}>{message}</p> : null}
      {error ? <p role="alert" style={styles.error}>{error}</p> : null}

      {data ? (
        <div style={styles.reviewBox}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12, padding: '12px 16px', background: '#f1f5f9', borderRadius: 8, border: '1px solid #cbd5e1' }}>
            <div>
              <strong>Bản dịch: </strong>
              <span>Revision {data.run.sha256.slice(0, 12)} · Trạng thái: {data.run.status}</span>
              <div style={{ fontSize: 13, color: '#64748b', marginTop: 4 }}>
                Tổng {data.segments.length} đoạn · {blockers.length} lỗi QA nghiêm trọng · {otherIssues.length} cảnh báo nhỏ
              </div>
            </div>

            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <span style={{ fontSize: 13, fontWeight: 700 }}>Lọc hiển thị:</span>
              <select
                value={filter}
                onChange={(e) => setFilter(e.target.value as typeof filter)}
                style={{ padding: '6px 10px', borderRadius: 6, border: '1px solid #cbd5e1', fontSize: 13 }}
              >
                <option value="ALL">Tất cả ({data.segments.length})</option>
                <option value="ISSUES_ONLY">Có vấn đề QA ({(data.issues ?? []).length})</option>
                <option value="BLOCKERS_ONLY">Chỉ lỗi nghiêm trọng ({blockers.length})</option>
              </select>
            </div>
          </div>

          {blockers.length > 0 ? (
            <div style={{ background: '#fffbeb', border: '1px solid #fde68a', borderRadius: 8, padding: 16 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#b45309', fontWeight: 700 }}>
                <span>⚠️</span>
                <span>Phát hiện {blockers.length} vấn đề QA nghiêm trọng (Blocker)</span>
              </div>
              <p style={{ margin: '8px 0', fontSize: 14, color: '#78350f' }}>
                Các vấn đề này có thể do chênh lệch độ dài, thiếu số liệu/đơn vị hoặc thuật ngữ khóa. Bạn có thể sửa trực tiếp câu dịch ở dưới hoặc chọn bỏ qua để duyệt tiếp sang bước lồng tiếng.
              </p>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
                <button
                  type="button"
                  onClick={() => void approve(true)}
                  disabled={busy}
                  style={{
                    padding: '8px 14px',
                    background: '#d97706',
                    color: '#ffffff',
                    border: 'none',
                    borderRadius: 6,
                    fontWeight: 700,
                    cursor: 'pointer',
                  }}
                >
                  Bỏ qua cảnh báo & Phê duyệt (Chấp nhận rủi ro)
                </button>
              </div>
            </div>
          ) : null}

          <div style={{ display: 'grid', gap: 12 }}>
            {displayedSegments.map((segment, index) => {
              const segIssues = (data.issues ?? []).filter((i) => i.sourceSegmentId === segment.sourceSegmentId);
              const isEditing = editingId === segment.sourceSegmentId;
              const isSaving = savingId === segment.sourceSegmentId;

              return (
                <article key={segment.sourceSegmentId} style={styles.segment}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span style={{ fontSize: 12, fontWeight: 700, color: '#64748b' }}>
                      Đoạn #{index + 1}
                    </span>
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                      {segIssues.map((issue) => (
                        <span
                          key={issue.id}
                          style={{
                            fontSize: 11,
                            padding: '2px 8px',
                            borderRadius: 4,
                            fontWeight: 700,
                            background:
                              issue.severity === 'MAJOR' || issue.severity === 'CRITICAL'
                                ? '#fee2e2'
                                : '#e0f2fe',
                            color:
                              issue.severity === 'MAJOR' || issue.severity === 'CRITICAL'
                                ? '#991b1b'
                                : '#075985',
                          }}
                          title={issue.suggestion ?? ''}
                        >
                          {issue.severity}: {issue.category} ({issue.evidence})
                        </span>
                      ))}
                    </div>
                  </div>

                  <div style={{ background: '#f8fafc', padding: 10, borderRadius: 6, borderLeft: '3px solid #94a3b8', fontSize: 14 }}>
                    <span style={{ fontSize: 11, color: '#64748b', display: 'block', marginBottom: 2 }}>GỐC:</span>
                    {segment.sourceText}
                  </div>

                  {isEditing ? (
                    <div style={{ display: 'grid', gap: 8 }}>
                      <textarea
                        value={drafts[segment.sourceSegmentId] ?? ''}
                        onChange={(e) =>
                          setDrafts({ ...drafts, [segment.sourceSegmentId]: e.target.value })
                        }
                        rows={3}
                        style={{
                          width: '100%',
                          padding: 10,
                          borderRadius: 6,
                          border: '1px solid #3b82f6',
                          fontSize: 14,
                          boxSizing: 'border-box',
                        }}
                      />
                      <div style={{ display: 'flex', gap: 8 }}>
                        <button
                          type="button"
                          onClick={() => void saveSegment(segment.sourceSegmentId)}
                          disabled={isSaving}
                          style={styles.primaryButton}
                        >
                          {isSaving ? 'Đang lưu...' : 'Lưu bản sửa'}
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            setDrafts({ ...drafts, [segment.sourceSegmentId]: segment.targetText });
                            setEditingId(null);
                          }}
                          style={styles.secondaryButton}
                        >
                          Hủy
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
                      <div style={{ fontSize: 15, lineHeight: 1.5, color: '#0f172a' }}>
                        <span style={{ fontSize: 11, color: '#64748b', display: 'block', marginBottom: 2 }}>BẢN DỊCH:</span>
                        {segment.targetText}
                      </div>
                      <button
                        type="button"
                        onClick={() => setEditingId(segment.sourceSegmentId)}
                        style={{ ...styles.secondaryButton, padding: '4px 10px', fontSize: 12, flexShrink: 0 }}
                      >
                        Sửa
                      </button>
                    </div>
                  )}
                </article>
              );
            })}
          </div>

          <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginTop: 16, flexWrap: 'wrap' }}>
            <button
              type="button"
              onClick={() => void approve(false)}
              disabled={busy || blockers.length > 0}
              style={{
                ...styles.primaryButton,
                opacity: blockers.length > 0 ? 0.6 : 1,
                cursor: blockers.length > 0 ? 'not-allowed' : 'pointer',
              }}
              title={blockers.length > 0 ? 'Cần giải quyết hoặc bỏ qua các lỗi QA nghiêm trọng trước khi duyệt' : ''}
            >
              Phê duyệt chuẩn ({data.segments.length} đoạn)
            </button>

            {blockers.length > 0 ? (
              <button
                type="button"
                onClick={() => void approve(true)}
                disabled={busy}
                style={{
                  ...styles.primaryButton,
                  background: '#d97706',
                  borderColor: '#b45309',
                }}
              >
                Bỏ qua cảnh báo QA & Phê duyệt tiếp
              </button>
            ) : null}
          </div>
        </div>
      ) : null}
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
      <JobsList />
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
