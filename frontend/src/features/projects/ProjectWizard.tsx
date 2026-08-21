import { ChangeEvent, FormEvent, useMemo, useState } from 'react';
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

const scopes = ['TRANSLATE_VI', 'CREATE_AUDIO', 'PUBLIC_STREAM'];

export function ProjectWizard() {
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
    <section style={styles.panel} aria-label="Tạo dự án và quyền">
      <div>
        <h1 style={styles.title}>Truyện Audio Studio</h1>
        <p style={styles.subtitle}>Tạo dự án local, khóa quyền, rồi đi thẳng vào luồng single narrator.</p>
      </div>

      <form onSubmit={(event) => void submit(event)} style={styles.form}>
        <label style={styles.label}>
          Tên truyện
          <input value={title} onChange={(event) => setTitle(event.target.value)} required style={styles.input} />
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
          Bằng chứng quyền
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
          Tạo dự án
        </button>
      </form>

      {error ? <p role="alert" style={styles.error}>{error}</p> : null}
      {project ? (
        <section style={styles.result} aria-label="Dự án đã tạo">
          <strong>{project.title}</strong>
          {evidence ? <span>Evidence hash {evidence.sha256.slice(0, 12)}</span> : null}
          <Link to={`/projects/${project.id}/import`} style={styles.link}>
            Nhập nội dung
          </Link>
        </section>
      ) : null}
    </section>
  );
}

const styles: Record<string, React.CSSProperties> = {
  panel: {
    display: 'grid',
    gap: 20,
    maxWidth: 820,
    margin: '0 auto',
    padding: 24,
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
    gap: 14,
  },
  label: {
    display: 'grid',
    gap: 6,
    fontWeight: 800,
  },
  input: {
    minHeight: 40,
    boxSizing: 'border-box',
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
  },
  error: {
    margin: 0,
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
