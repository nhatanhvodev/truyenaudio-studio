import { ChangeEvent, FormEvent, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { apiForm, apiJson } from '../../shared/api';

type ProjectResponse = {
  id: string;
  title: string;
};

type EvidenceResponse = {
  id: string;
  sha256: string;
};

type ChapterSummary = {
  id: string;
  ordinal: number;
  title: string | null;
  state: string;
};

type ProjectItem = {
  id: string;
  title: string;
  slug: string;
  sourceType: string;
  rightsStatus: string;
  createdAt: string | null;
  updatedAt: string | null;
  chapterCount: number;
  firstChapterId: string | null;
  chapters: ChapterSummary[];
};

const scopes = ['TRANSLATE_VI', 'CREATE_AUDIO', 'PUBLIC_STREAM'];

/** U03: the library mounts at most this many rows per request (server caps at 100). */
const LIBRARY_PAGE_SIZE = 20;

export function ProjectWizard() {
  const [projects, setProjects] = useState<ProjectItem[]>([]);
  const [loadingProjects, setLoadingProjects] = useState(false);
  const [showCreateForm, setShowCreateForm] = useState(true);
  const [expandedProjectId, setExpandedProjectId] = useState<string | null>(null);
  // U03: the library is cursor-paginated on the server, so the screen keeps the
  // cursor, the total and the current filter instead of loading everything.
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [pageTotal, setPageTotal] = useState<number | null>(null);
  const [libraryQuery, setLibraryQuery] = useState('');

  const [title, setTitle] = useState('');
  const [sourceType, setSourceType] = useState('SELF_AUTHORED');
  const [rightsStatus, setRightsStatus] = useState('CLEARED');
  const [evidenceFile, setEvidenceFile] = useState<File | null>(null);
  const [checkedScopes, setCheckedScopes] = useState<Record<string, boolean>>({
    TRANSLATE_VI: false,
    CREATE_AUDIO: false,
    PUBLIC_STREAM: false,
  });
  const [project, setProject] = useState<ProjectResponse | null>(null);
  const [evidence, setEvidence] = useState<EvidenceResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    void loadProjects();
  }, []);

  async function loadProjects(options: { cursor?: string | null; append?: boolean } = {}) {
    setLoadingProjects(true);
    try {
      const params = new URLSearchParams({ limit: String(LIBRARY_PAGE_SIZE) });
      if (options.cursor) {
        params.set('cursor', options.cursor);
      }
      if (libraryQuery.trim()) {
        params.set('q', libraryQuery.trim());
      }
      const resp = await apiJson<{
        projects: ProjectItem[];
        page?: { total: number; nextCursor: string | null; hasMore: boolean; limit: number };
      }>(`/api/projects?${params.toString()}`);
      setProjects((current) => (options.append ? [...current, ...resp.projects] : resp.projects));
      // A backend without page metadata (older build) simply has no next page.
      setNextCursor(resp.page?.nextCursor ?? null);
      setPageTotal(resp.page?.total ?? null);
      if (!options.append && resp.projects.length === 0) {
        setShowCreateForm(true);
      }
    } catch {
      // ignore
    } finally {
      setLoadingProjects(false);
    }
  }

  const slug = useMemo(() => {
    const normalized = title
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-|-$/g, '');
    return `${normalized || 'truyen'}-${Date.now().toString(36)}`;
  }, [title]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      const created = await apiJson<ProjectResponse>('/api/projects', {
        method: 'POST',
        body: {
          title,
          slug,
          source_type: sourceType,
          rights_status: rightsStatus,
        },
      });
      let evidenceId: string | null = null;
      if (evidenceFile) {
        const form = new FormData();
        form.set('file', evidenceFile);
        form.set('evidence_kind', 'AUTHOR_PERMISSION');
        form.set('issuer', 'LOCAL_OWNER');
        const uploaded = await apiForm<EvidenceResponse>(`/api/projects/${created.id}/rights/evidence`, form);
        evidenceId = uploaded.id;
        setEvidence(uploaded);
      }
      const validFrom = new Date(Date.now() - 60_000).toISOString();
      for (const scope of scopes) {
        if (!checkedScopes[scope]) {
          continue;
        }
        await apiJson(`/api/projects/${created.id}/rights/grants`, {
          method: 'POST',
          body: {
            scope,
            territory: 'VN',
            allows_ai_processing: true,
            allows_third_party_cloud: false,
            valid_from: validFrom,
            evidence_id: evidenceId,
          },
        });
      }
      setProject(created);
      setTitle('');
      await loadProjects();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'PROJECT_CREATE_FAILED');
    } finally {
      setBusy(false);
    }
  }

  function toggleScope(scope: string) {
    setCheckedScopes((current) => ({ ...current, [scope]: !current[scope] }));
  }

  return (
    <section style={styles.panel} aria-label="Quản lý dự án">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 16 }}>
        <div style={{ minWidth: 0 }}>
          <h1 style={styles.title}>Truyện Audio Studio</h1>
          <p style={styles.subtitle}>Quản lý dự án âm thanh, cào truyện Wenku, dịch Hán-Việt & lồng tiếng AI.</p>
        </div>

        <button
          type="button"
          onClick={() => setShowCreateForm(!showCreateForm)}
          style={{
            ...styles.primaryButton,
            background: showCreateForm ? '#475467' : '#155eef',
            display: 'flex',
            alignItems: 'center',
            gap: 6,
          }}
        >
          {showCreateForm ? '✕ Thu gọn form tạo' : '+ Tạo dự án mới'}
        </button>
      </div>

      {/* Danh sách các dự án đã tạo */}
      <section aria-label="Danh sách dự án" style={{ display: 'grid', gap: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <h2 style={{ margin: 0, fontSize: 20, color: '#0f172a' }}>
            Dự án hiện có ({projects.length}
            {pageTotal !== null ? `/${pageTotal}` : ''})
          </h2>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <label style={{ display: 'grid', gap: 4, fontSize: 12, fontWeight: 700 }}>
              Tìm dự án
              <input
                aria-label="Tìm dự án"
                value={libraryQuery}
                onChange={(event) => setLibraryQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault();
                    void loadProjects();
                  }
                }}
                style={{ ...styles.input, minHeight: 34, fontSize: 13 }}
              />
            </label>
            <button
              type="button"
              onClick={() => void loadProjects()}
              disabled={loadingProjects}
              style={{ ...styles.secondaryButton, padding: '4px 10px', fontSize: 13 }}
            >
              {loadingProjects ? 'Đang tải...' : 'Tìm / Làm mới'}
            </button>
          </div>
        </div>

        {loadingProjects && projects.length === 0 ? (
          <p style={{ color: '#64748b', fontStyle: 'italic' }}>Đang tải danh sách dự án...</p>
        ) : null}

        {projects.length === 0 && !loadingProjects ? (
          <div style={{ padding: 24, textAlign: 'center', background: '#f8fafc', borderRadius: 8, border: '1px dashed #cbd5e1' }}>
            <p style={{ margin: '0 0 12px', color: '#64748b' }}>Chưa có dự án nào được tạo. Hãy tạo dự án đầu tiên bên dưới!</p>
            <button
              type="button"
              onClick={() => setShowCreateForm(true)}
              style={styles.primaryButton}
            >
              + Tạo dự án ngay
            </button>
          </div>
        ) : null}

        <div style={{ display: 'grid', gap: 16 }}>
          {projects.map((p) => {
            const isExpanded = expandedProjectId === p.id;
            return (
              <article
                key={p.id}
                style={{
                  padding: 16,
                  borderRadius: 8,
                  border: '1px solid #cbd5e1',
                  background: '#ffffff',
                  boxShadow: '0 1px 3px rgba(0,0,0,0.05)',
                  display: 'grid',
                  gap: 12,
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 12 }}>
                  <div>
                    <h3 style={{ margin: 0, fontSize: 18, color: '#1e293b' }}>{p.title}</h3>
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 6 }}>
                      <span
                        style={{
                          fontSize: 12,
                          fontWeight: 700,
                          padding: '2px 8px',
                          borderRadius: 12,
                          background: p.chapterCount > 0 ? '#dcfce7' : '#f1f5f9',
                          color: p.chapterCount > 0 ? '#15803d' : '#64748b',
                        }}
                      >
                        📚 {p.chapterCount} chương đã cào
                      </span>
                      <span style={{ fontSize: 12, padding: '2px 8px', borderRadius: 12, background: '#f1f5f9', color: '#475467' }}>
                        Quyền: {p.rightsStatus}
                      </span>
                      {p.createdAt ? (
                        <span style={{ fontSize: 12, color: '#94a3b8' }}>
                          Tạo lúc: {new Date(p.createdAt).toLocaleDateString('vi-VN')}
                        </span>
                      ) : null}
                    </div>
                  </div>

                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                    {p.firstChapterId ? (
                      <Link
                        to={`/chapters/${p.firstChapterId}/translation`}
                        style={{
                          ...styles.primaryButton,
                          textDecoration: 'none',
                          padding: '8px 14px',
                          fontSize: 14,
                        }}
                      >
                        Dịch & Lồng tiếng (Chương 1) →
                      </Link>
                    ) : null}

                    <Link
                      to={`/projects/${p.id}/import`}
                      style={{
                        ...styles.secondaryButton,
                        textDecoration: 'none',
                        padding: '8px 12px',
                        fontSize: 14,
                      }}
                    >
                      + Cào thêm chương (Wenku)
                    </Link>

                    <Link
                      to={`/projects/${p.id}/batch`}
                      style={{
                        ...styles.secondaryButton,
                        textDecoration: 'none',
                        padding: '8px 12px',
                        fontSize: 14,
                      }}
                    >
                      Hàng đợi (Batch)
                    </Link>
                  </div>
                </div>

                {p.chapterCount > 0 ? (
                  <div>
                    <button
                      type="button"
                      onClick={() => setExpandedProjectId(isExpanded ? null : p.id)}
                      style={{
                        background: 'none',
                        border: 'none',
                        padding: 0,
                        color: '#2563eb',
                        fontSize: 13,
                        fontWeight: 700,
                        cursor: 'pointer',
                      }}
                    >
                      {isExpanded ? '▲ Thu gọn danh sách chương' : `▼ Xem danh sách ${p.chapterCount} chương đã cào`}
                    </button>

                    {isExpanded ? (
                      <div
                        style={{
                          marginTop: 10,
                          padding: 12,
                          background: '#f8fafc',
                          borderRadius: 6,
                          border: '1px solid #e2e8f0',
                          display: 'grid',
                          gap: 6,
                          maxHeight: 280,
                          overflowY: 'auto',
                        }}
                      >
                        {p.chapters.map((ch) => (
                          <div
                            key={ch.id}
                            style={{
                              display: 'flex',
                              justifyContent: 'space-between',
                              alignItems: 'center',
                              padding: '6px 8px',
                              background: '#ffffff',
                              borderRadius: 4,
                              fontSize: 13,
                            }}
                          >
                            <span style={{ fontWeight: 600 }}>
                              Chương {ch.ordinal}: {ch.title || '(Không có tiêu đề)'}
                            </span>
                            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                              <span
                                style={{
                                  fontSize: 11,
                                  padding: '2px 6px',
                                  borderRadius: 4,
                                  background: ch.state.includes('APPROVED') ? '#dcfce7' : '#f1f5f9',
                                  color: ch.state.includes('APPROVED') ? '#15803d' : '#64748b',
                                  fontWeight: 600,
                                }}
                              >
                                {ch.state}
                              </span>
                              <Link
                                to={`/chapters/${ch.id}/translation`}
                                style={{ color: '#2563eb', textDecoration: 'none', fontWeight: 600, fontSize: 12 }}
                              >
                                Mở dịch →
                              </Link>
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </article>
            );
          })}
        </div>

        {nextCursor ? (
          <button
            type="button"
            onClick={() => void loadProjects({ cursor: nextCursor, append: true })}
            disabled={loadingProjects}
            style={{ ...styles.secondaryButton, justifySelf: 'center' }}
          >
            {loadingProjects ? 'Đang tải...' : 'Tải thêm dự án'}
          </button>
        ) : null}
      </section>

      {/* Form tạo dự án mới */}
      {showCreateForm ? (
        <section
          style={{
            padding: 20,
            borderRadius: 8,
            border: '1px solid #cbd5e1',
            background: '#f8fafc',
            marginTop: 8,
          }}
          aria-label="Form tạo dự án mới"
        >
          <h2 style={{ margin: '0 0 16px', fontSize: 20, color: '#0f172a' }}>Tạo dự án mới</h2>
          <form onSubmit={(event) => void submit(event)} style={styles.form}>
            <label style={styles.label}>
              Tên truyện
              <input
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                required
                style={styles.input}
                placeholder="VD: Sau 1 năm nhập học, nữ giáo sư đã bị tôi lừa đi"
              />
            </label>
            <label style={styles.label}>
              Loại nguồn
              <select value={sourceType} onChange={(event) => setSourceType(event.target.value)} style={styles.input}>
                <option value="SELF_AUTHORED">SELF_AUTHORED</option>
                <option value="LICENSED_PARTNER">LICENSED_PARTNER</option>
                <option value="USER_SUPPLIED_PRIVATE">USER_SUPPLIED_PRIVATE</option>
              </select>
            </label>
            <label style={styles.label}>
              Trạng thái quyền
              <select value={rightsStatus} onChange={(event) => setRightsStatus(event.target.value)} style={styles.input}>
                <option value="CLEARED">CLEARED</option>
                <option value="REVIEW_REQUIRED">REVIEW_REQUIRED</option>
                <option value="PRIVATE_ONLY">PRIVATE_ONLY</option>
              </select>
            </label>
            <label style={styles.label}>
              Bằng chứng quyền (Tùy chọn)
              <input
                type="file"
                onChange={(event: ChangeEvent<HTMLInputElement>) => setEvidenceFile(event.target.files?.[0] ?? null)}
                style={styles.input}
              />
            </label>
            <fieldset style={styles.fieldset}>
              <legend style={styles.legend}>Phạm vi quyền</legend>
              {scopes.map((scope) => (
                <label key={scope} style={styles.checkboxLabel}>
                  <input type="checkbox" checked={checkedScopes[scope]} onChange={() => toggleScope(scope)} />
                  {scope}
                </label>
              ))}
            </fieldset>
            <button type="submit" disabled={busy} style={styles.primaryButton}>
              {busy ? 'Đang tạo...' : 'Tạo dự án'}
            </button>
          </form>

          {error ? <p role="alert" style={styles.error}>{error}</p> : null}
          {project ? (
            <section style={{ ...styles.result, marginTop: 16 }} aria-label="Dự án đã tạo">
              <strong>Đã tạo thành công: {project.title}</strong>
              {evidence ? <span>Evidence hash {evidence.sha256.slice(0, 12)}</span> : null}
              <Link to={`/projects/${project.id}/import`} style={styles.link}>
                Bắt đầu cào chương từ Wenku →
              </Link>
            </section>
          ) : null}
        </section>
      ) : null}
    </section>
  );
}

const styles: Record<string, React.CSSProperties> = {
  panel: {
    display: 'grid',
    // `minmax(0, 1fr)` keeps the single column from being floored by its
    // widest child's intrinsic width, so the wizard reflows on 320/390px
    // viewports instead of forcing a horizontal scrollbar (G-UX responsive).
    gridTemplateColumns: 'minmax(0, 1fr)',
    gap: 24,
    maxWidth: 960,
    margin: '0 auto',
    padding: 24,
    boxSizing: 'border-box',
  },
  title: {
    margin: 0,
    fontSize: 28,
    letterSpacing: 0,
  },
  subtitle: {
    margin: '8px 0 0',
    color: '#586274',
  },
  form: {
    display: 'grid',
    // Same reason as `panel`: an implicit `auto` column is floored by the
    // widest control's max-content width (the source/rights `<select>`s), so
    // pin the column to the available width instead.
    gridTemplateColumns: 'minmax(0, 1fr)',
    gap: 14,
    minWidth: 0,
  },
  label: {
    display: 'grid',
    gap: 6,
    fontWeight: 800,
    minWidth: 0,
  },
  input: {
    minHeight: 40,
    boxSizing: 'border-box',
    width: '100%',
    minWidth: 0,
    padding: '8px 10px',
    border: '1px solid #c9d3df',
    borderRadius: 6,
    background: '#ffffff',
    font: 'inherit',
  },
  fieldset: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: 12,
    margin: 0,
    padding: 12,
    border: '1px solid #d7dde8',
    borderRadius: 8,
  },
  legend: {
    fontWeight: 800,
  },
  checkboxLabel: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    fontWeight: 700,
  },
  primaryButton: {
    justifySelf: 'start',
    padding: '10px 14px',
    border: 0,
    borderRadius: 6,
    background: '#155eef',
    color: '#ffffff',
    fontWeight: 900,
    cursor: 'pointer',
  },
  secondaryButton: {
    padding: '8px 14px',
    border: '1px solid #cbd5e1',
    borderRadius: 6,
    background: '#ffffff',
    color: '#334155',
    fontWeight: 700,
    cursor: 'pointer',
  },
  error: {
    margin: '12px 0 0',
    color: '#9a3412',
    fontWeight: 800,
  },
  result: {
    display: 'flex',
    flexWrap: 'wrap',
    alignItems: 'center',
    gap: 12,
    padding: 14,
    border: '1px solid #c7ead2',
    borderRadius: 8,
    background: '#f0fdf4',
  },
  link: {
    color: '#0b5cad',
    fontWeight: 900,
    textDecoration: 'none',
  },
};
