import { Link, useLocation, useNavigate, useParams } from 'react-router-dom';
import { useEffect, useState } from 'react';
import { apiJson } from '../../shared/api';
import ArtifactPlayer from '../../features/audio/ArtifactPlayer';

export type RenderedAudio = {
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

export function AudioScreen() {
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

const styles: Record<string, React.CSSProperties> = {
  error: {
    margin: 0,
    color: '#9a3412',
    fontWeight: 900,
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
  success: {
    margin: 0,
    color: '#166534',
    fontWeight: 900,
  },
  title: {
    margin: 0,
    fontSize: 26,
    letterSpacing: 0,
  },
};
