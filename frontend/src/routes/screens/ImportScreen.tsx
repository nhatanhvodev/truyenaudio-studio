import { Navigate, useNavigate, useParams } from 'react-router-dom';
import { useState } from 'react';
import { WenkuImport } from '../../features/import/WenkuImport';
import ImportPreview, { type ImportCandidate } from '../../features/import/ImportPreview';
import { apiForm, apiJson } from '../../shared/api';

type Chapter = {
  id: string;
  project_id?: string;
  projectId?: string;
  ordinal: number;
  source_title?: string | null;
  sourceTitle?: string | null;
};

export function ImportScreen() {
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

const styles: Record<string, React.CSSProperties> = {
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
  input: {
    width: '100%',
    minHeight: 40,
    boxSizing: 'border-box',
    padding: '8px 10px',
    border: '1px solid #c9d3df',
    borderRadius: 6,
    font: 'inherit',
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
  guardBox: {
    display: 'grid',
    gap: 12,
    padding: 12,
    border: '1px solid #d7dde8',
    borderRadius: 8,
    background: '#f8fafc',
  },
  error: {
    margin: 0,
    color: '#9a3412',
    fontWeight: 900,
  },
};
