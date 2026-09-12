import { Navigate, useNavigate, useParams } from 'react-router-dom';
import { useState } from 'react';
import { WenkuImport } from '../../features/import/WenkuImport';
import ImportPreview, { type ImportCandidate } from '../../features/import/ImportPreview';
import { apiForm, apiJson } from '../../shared/api';

import styles from './ImportScreen.module.css';

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
    <section className={styles.panel} aria-label="Nhập nội dung">
      <h1 className={styles.title}>Nhập nội dung</h1>
      <WenkuImport
        projectId={projectId}
        onImportSuccess={(firstChapterId) => {
          navigate(`/chapters/${firstChapterId}/translation`);
        }}
      />

      <div className={styles.fileSection}>
        <h2 className={styles.fileTitle}>Hoặc nhập từ File / Thư mục máy tính</h2>
        <div className={styles.fileGrid}>
          <section className={styles.guardBox} aria-label="Folder import preview">
            <label className={styles.label}>
              Local folder path
              <input value={folderPath} onChange={(event) => setFolderPath(event.target.value)} className={styles.input} />
            </label>
            <button type="button" onClick={() => void previewFolder()} disabled={busy || !folderPath.trim()} className={styles.secondaryButton}>
              Preview folder
            </button>
          </section>

          <section className={styles.guardBox} aria-label="Book import preview">
            <label className={styles.label}>
              EPUB or DOCX file
              <input
                type="file"
                accept=".epub,.docx,application/epub+zip,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                onChange={(event) => setBookFile(event.target.files?.[0] ?? null)}
                className={styles.input}
              />
            </label>
            <div className={styles.actions}>
              <button type="button" onClick={() => void previewBook('EPUB')} disabled={busy || !bookFile} className={styles.secondaryButton}>
                Preview EPUB
              </button>
              <button type="button" onClick={() => void previewBook('DOCX')} disabled={busy || !bookFile} className={styles.secondaryButton}>
                Preview DOCX
              </button>
            </div>
          </section>
        </div>

        {candidates.length > 0 ? <ImportPreview candidates={candidates} onConfirm={(mapped) => void confirmPreview(mapped)} /> : null}
        {error ? <p role="alert" className={styles.error}>{error}</p> : null}
      </div>
    </section>
  );
}

