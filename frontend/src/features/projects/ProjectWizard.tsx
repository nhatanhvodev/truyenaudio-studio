import { ChangeEvent, FormEvent, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import styles from './ProjectWizard.module.css';
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
    <section className={styles.panel} aria-label="Quản lý dự án">
      <div className={styles.pageHead}>
        <div className={styles.pageHeadMain}>
          <h1 className={styles.pageTitle}>Truyện Audio Studio</h1>
          <p className={styles.subtitle}>Quản lý dự án âm thanh, cào truyện Wenku, dịch Hán-Việt & lồng tiếng AI.</p>
        </div>

        <button
          type="button"
          onClick={() => setShowCreateForm(!showCreateForm)}
          className={`${styles.toggleButton} ${showCreateForm ? styles.secondaryButton : styles.primaryButton}`}
        >
          {showCreateForm ? '✕ Thu gọn form tạo' : '+ Tạo dự án mới'}
        </button>
      </div>

      {/* Danh sách các dự án đã tạo */}
      <section aria-label="Danh sách dự án" className={styles.list}>
        <div className={styles.listHead}>
          <h2 className={styles.pageCount}>
            Dự án hiện có ({projects.length}
            {pageTotal !== null ? `/${pageTotal}` : ''})
          </h2>
          <div className={styles.search}>
            <label className={styles.field}>
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
                className={`${styles.input} ${styles.inputCompact}`}
              />
            </label>
            <button
              type="button"
              onClick={() => void loadProjects()}
              disabled={loadingProjects}
              className={styles.secondaryButton}
            >
              {loadingProjects ? 'Đang tải...' : 'Tìm / Làm mới'}
            </button>
          </div>
        </div>

        {loadingProjects && projects.length === 0 ? (
          <p className={styles.loadingNote}>Đang tải danh sách dự án...</p>
        ) : null}

        {projects.length === 0 && !loadingProjects ? (
          <div className={styles.emptyBox}>
            <p className={styles.emptyText}>Chưa có dự án nào được tạo. Hãy tạo dự án đầu tiên bên dưới!</p>
            <button
              type="button"
              onClick={() => setShowCreateForm(true)}
              className={styles.primaryButton}
            >
              + Tạo dự án ngay
            </button>
          </div>
        ) : null}

        <div className={styles.rows}>
          {projects.map((p) => {
            const isExpanded = expandedProjectId === p.id;
            return (
              <article
                key={p.id}
                className={styles.row}
              >
                <div className={styles.rowMain}>
                  <div className={styles.rowTitle}>
                    <h3 className={styles.title}>{p.title}</h3>
                    <div className={styles.badges}>
                      <span className={`${styles.badge} ${p.chapterCount > 0 ? styles.badgeDone : styles.badgeMuted}`}>
                        📚 {p.chapterCount} chương đã cào
                      </span>
                      <span className={`${styles.badge} ${styles.badgeMuted}`}>
                        Quyền: {p.rightsStatus}
                      </span>
                      {p.createdAt ? (
                        <span className={styles.meta}>
                          Tạo lúc: {new Date(p.createdAt).toLocaleDateString('vi-VN')}
                        </span>
                      ) : null}
                    </div>
                  </div>

                  <div className={styles.actions}>
                    {p.firstChapterId ? (
                      <Link
                        to={`/chapters/${p.firstChapterId}/translation`}
                        className={styles.primaryButton}
                      >
                        Dịch & Lồng tiếng (Chương 1) →
                      </Link>
                    ) : null}

                    <Link
                      to={`/projects/${p.id}/import`}
                      className={styles.secondaryButton}
                    >
                      + Cào thêm chương (Wenku)
                    </Link>

                    <Link
                      to={`/projects/${p.id}/batch`}
                      className={styles.secondaryButton}
                    >
                      Hàng đợi (Batch)
                    </Link>
                  </div>
                </div>

                {p.chapterCount > 0 ? (
                  <div className={styles.rowWide}>
                    <button
                      type="button"
                      onClick={() => setExpandedProjectId(isExpanded ? null : p.id)}
                      className={styles.expandButton}
                    >
                      {isExpanded ? '▲ Thu gọn danh sách chương' : `▼ Xem danh sách ${p.chapterCount} chương đã cào`}
                    </button>

                    {isExpanded ? (
                      <div className={styles.chapters}>
                        {p.chapters.map((ch) => (
                          <div
                            key={ch.id}
                            className={styles.chapterRow}
                          >
                            <span className={styles.chapterTitle}>
                              Chương {ch.ordinal}: {ch.title || '(Không có tiêu đề)'}
                            </span>
                            <div className={styles.chapterActions}>
                              <span
                                className={`${styles.chapterState} ${ch.state.includes('APPROVED') ? styles.chapterStateApproved : styles.chapterStateMuted}`}
                              >
                                {ch.state}
                              </span>
                              <Link
                                to={`/chapters/${ch.id}/translation`}
                                className={styles.chapterLink}
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
            className={`${styles.secondaryButton} ${styles.pager} ${styles.pagerButton}`}
          >
            {loadingProjects ? 'Đang tải...' : 'Tải thêm dự án'}
          </button>
        ) : null}
      </section>

      {/* Form tạo dự án mới */}
      {showCreateForm ? (
        <section
          className={styles.createPanel}
          aria-label="Form tạo dự án mới"
        >
          <h2 className={styles.sectionTitle}>Tạo dự án mới</h2>
          <form onSubmit={(event) => void submit(event)} className={styles.form}>
            <label className={styles.label}>
              Tên truyện
              <input
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                required
                className={styles.input}
                placeholder="VD: Sau 1 năm nhập học, nữ giáo sư đã bị tôi lừa đi"
              />
            </label>
            <label className={styles.label}>
              Loại nguồn
              <select value={sourceType} onChange={(event) => setSourceType(event.target.value)} className={styles.input}>
                <option value="SELF_AUTHORED">SELF_AUTHORED</option>
                <option value="LICENSED_PARTNER">LICENSED_PARTNER</option>
                <option value="USER_SUPPLIED_PRIVATE">USER_SUPPLIED_PRIVATE</option>
              </select>
            </label>
            <label className={styles.label}>
              Trạng thái quyền
              <select value={rightsStatus} onChange={(event) => setRightsStatus(event.target.value)} className={styles.input}>
                <option value="CLEARED">CLEARED</option>
                <option value="REVIEW_REQUIRED">REVIEW_REQUIRED</option>
                <option value="PRIVATE_ONLY">PRIVATE_ONLY</option>
              </select>
            </label>
            <label className={styles.label}>
              Bằng chứng quyền (Tùy chọn)
              <input
                type="file"
                onChange={(event: ChangeEvent<HTMLInputElement>) => setEvidenceFile(event.target.files?.[0] ?? null)}
                className={styles.input}
              />
            </label>
            <fieldset className={styles.fieldset}>
              <legend className={styles.legend}>Phạm vi quyền</legend>
              {scopes.map((scope) => (
                <label key={scope} className={styles.checkboxLabel}>
                  <input type="checkbox" checked={checkedScopes[scope]} onChange={() => toggleScope(scope)} />
                  {scope}
                </label>
              ))}
            </fieldset>
            <button type="submit" disabled={busy} className={styles.primaryButton}>
              {busy ? 'Đang tạo...' : 'Tạo dự án'}
            </button>
          </form>

          {error ? <p role="alert" className={styles.error}>{error}</p> : null}
          {project ? (
            <section className={styles.result} aria-label="Dự án đã tạo">
              <strong>Đã tạo thành công: {project.title}</strong>
              {evidence ? <span>Evidence hash {evidence.sha256.slice(0, 12)}</span> : null}
              <Link to={`/projects/${project.id}/import`} className={styles.link}>
                Bắt đầu cào chương từ Wenku →
              </Link>
            </section>
          ) : null}
        </section>
      ) : null}
    </section>
  );
}
