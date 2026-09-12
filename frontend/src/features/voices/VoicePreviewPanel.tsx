import { useEffect, useRef, useState } from 'react';
import styles from './VoicePreviewPanel.module.css';

export const PREVIEW_TEXT_LIMIT = 420;
export const DEFAULT_SAMPLE_TEXT = 'Xin chào, đây là bản nghe thử giọng đọc tiếng Việt.';

export type VoiceOption = {
  id: string;
  name: string;
  locale: string;
  available: boolean;
  activationHint?: string;
};

export type PreviewJobView = {
  jobId: string;
  presetId: string;
  textKind: string;
  status: string;
  cacheKey: string;
  fromCache: boolean;
  audioUrl?: string | null;
  durationMs?: number | null;
  reason?: string | null;
};

type Props = {
  voices: VoiceOption[];
  sampleText?: string;
  selectedVoiceId?: string | null;
  onSelect?: (voiceId: string) => void;
};

export function previewContentUrl(jobId: string): string {
  return `/api/voices/preview-jobs/${jobId}/content`;
}

const POLL_INTERVAL_MS = 400;
const POLL_ATTEMPTS = 30;
const TERMINAL = ['READY', 'FAILED', 'CANCELLED'];

/**
 * A02 — màn chọn giọng có nghe thử.
 *
 * Một player duy nhất dùng chung cho mọi job (playing khác selected), văn bản mẫu hoặc văn bản tự nhập
 * (1..420 ký tự), so sánh A/B nhiều giọng trên CÙNG một văn bản, và giữ nguyên voice ID khi chọn.
 * Giọng chưa khả dụng thì không hiện nút điều khiển không hỗ trợ (chỉ hiện hướng dẫn cài model/license).
 */
export default function VoicePreviewPanel({
  voices,
  sampleText = DEFAULT_SAMPLE_TEXT,
  selectedVoiceId = null,
  onSelect,
}: Props) {
  const [textKind, setTextKind] = useState<'SAMPLE' | 'CUSTOM'>('SAMPLE');
  const [customText, setCustomText] = useState('');
  const [jobs, setJobs] = useState<Record<string, PreviewJobView>>({});
  const [compareJobs, setCompareJobs] = useState<PreviewJobView[]>([]);
  const [compareIds, setCompareIds] = useState<string[]>([]);
  const [playingJobId, setPlayingJobId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const pollRef = useRef<number | null>(null);

  const text = textKind === 'SAMPLE' ? sampleText : customText;
  const trimmed = text.trim();
  const tooLong = trimmed.length > PREVIEW_TEXT_LIMIT;
  const textError = trimmed.length === 0 ? 'PREVIEW_TEXT_EMPTY' : tooLong ? 'PREVIEW_TEXT_TOO_LONG' : '';

  useEffect(() => {
    return () => {
      if (pollRef.current !== null) {
        window.clearTimeout(pollRef.current);
      }
    };
  }, []);

  async function requestJson(url: string, body?: object): Promise<PreviewJobView> {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body ?? {}),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(String((payload as { detail?: unknown }).detail ?? 'VOICE_PREVIEW_FAILED'));
    }
    return payload as PreviewJobView;
  }

  async function loadJob(jobId: string): Promise<PreviewJobView> {
    const response = await fetch(`/api/voices/preview-jobs/${jobId}`);
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(String((payload as { detail?: unknown }).detail ?? 'VOICE_PREVIEW_FAILED'));
    }
    return payload as PreviewJobView;
  }

  /** Theo dõi job đang xếp hàng cho tới khi kết thúc; dừng khi component đổi job. */
  function pollJob(jobId: string, attempt = 0) {
    pollRef.current = window.setTimeout(() => {
      void loadJob(jobId)
        .then((job) => {
          setJobs((current) => ({ ...current, [job.presetId]: job }));
          if (!TERMINAL.includes(job.status) && attempt + 1 < POLL_ATTEMPTS) {
            pollJob(jobId, attempt + 1);
          }
        })
        .catch((reason: unknown) => {
          setError(reason instanceof Error ? reason.message : 'VOICE_PREVIEW_FAILED');
        });
    }, POLL_INTERVAL_MS);
  }

  async function createPreview(voice: VoiceOption, kind: 'SAMPLE' | 'CUSTOM' = textKind) {
    if (textError) {
      setError(textError);
      return;
    }
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const job = await requestJson('/api/voices/preview-jobs', {
        presetId: voice.id,
        text: kind === 'SAMPLE' ? sampleText : customText,
        textKind: kind,
      });
      setJobs((current) => ({ ...current, [voice.id]: job }));
      if (!TERMINAL.includes(job.status)) {
        pollJob(job.jobId);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'VOICE_PREVIEW_FAILED');
    } finally {
      setBusy(false);
    }
  }

  async function cancelPreview(voice: VoiceOption) {
    const job = jobs[voice.id];
    if (!job) {
      return;
    }
    try {
      const cancelled = await requestJson(`/api/voices/preview-jobs/${job.jobId}/cancel`);
      setJobs((current) => ({ ...current, [voice.id]: cancelled }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'VOICE_PREVIEW_CANCEL_FAILED');
    }
  }

  async function retryPreview(voice: VoiceOption) {
    const job = jobs[voice.id];
    if (!job) {
      await createPreview(voice);
      return;
    }
    try {
      const retried = await requestJson(`/api/voices/preview-jobs/${job.jobId}/retry`);
      setJobs((current) => ({ ...current, [voice.id]: retried }));
      if (!TERMINAL.includes(retried.status)) {
        pollJob(retried.jobId);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'VOICE_PREVIEW_RETRY_FAILED');
    }
  }

  function toggleCompare(voiceId: string) {
    setCompareIds((current) =>
      current.includes(voiceId)
        ? current.filter((id) => id !== voiceId)
        : current.length >= 3
          ? current
          : [...current, voiceId],
    );
  }

  async function compareSelected() {
    if (compareIds.length < 2) {
      setError('PREVIEW_COMPARE_REQUIRES_TWO');
      return;
    }
    if (textError) {
      setError(textError);
      return;
    }
    setBusy(true);
    setError('');
    try {
      const response = await fetch('/api/voices/preview-jobs/compare', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          presetIds: compareIds,
          text: textKind === 'SAMPLE' ? sampleText : customText,
        }),
      });
      const payload = (await response.json()) as { jobs?: PreviewJobView[]; detail?: string };
      if (!response.ok) {
        throw new Error(String(payload.detail ?? 'VOICE_PREVIEW_COMPARE_FAILED'));
      }
      setCompareJobs(payload.jobs ?? []);
      setNotice('Đang so sánh trên cùng một văn bản.');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'VOICE_PREVIEW_COMPARE_FAILED');
    } finally {
      setBusy(false);
    }
  }

  async function play(job: PreviewJobView) {
    const audio = audioRef.current;
    if (!audio) {
      return;
    }
    setError('');
    if (playingJobId === job.jobId) {
      audio.pause();
      setPlayingJobId(null);
      return;
    }
    audio.src = job.audioUrl ?? previewContentUrl(job.jobId);
    setPlayingJobId(job.jobId);
    try {
      await audio.play();
    } catch {
      setPlayingJobId(null);
      setError('VOICE_PREVIEW_PLAYBACK_FAILED');
    }
  }

  function select(voice: VoiceOption) {
    setNotice(`Đã chọn giọng ${voice.name}`);
    onSelect?.(voice.id);
  }

  return (
    <section aria-label="Nghe thử và chọn giọng" className={styles.shell}>
      <audio ref={audioRef} preload="none" data-testid="preview-audio" />

      <fieldset className={styles.fieldset}>
        <legend className={styles.legend}>Văn bản nghe thử</legend>
        <div className={styles.radios}>
          <label className={styles.radio}>
            <input
              type="radio"
              name="preview-text-kind"
              checked={textKind === 'SAMPLE'}
              onChange={() => setTextKind('SAMPLE')}
            />
            Văn bản mẫu
          </label>
          <label className={styles.radio}>
            <input
              type="radio"
              name="preview-text-kind"
              checked={textKind === 'CUSTOM'}
              onChange={() => setTextKind('CUSTOM')}
            />
            Văn bản tự nhập
          </label>
        </div>
        {textKind === 'CUSTOM' ? (
          <label className={styles.textField}>
            <span>Văn bản nghe thử</span>
            <textarea
              aria-label="Văn bản nghe thử"
              value={customText}
              rows={3}
              onChange={(event) => setCustomText(event.target.value)}
              className={styles.textarea}
            />
          </label>
        ) : (
          <p className={styles.sample}>{sampleText}</p>
        )}
        <p role="status" className={tooLong ? styles.error : styles.counter}>
          {trimmed.length}/{PREVIEW_TEXT_LIMIT} ký tự
        </p>
      </fieldset>

      {error ? <p role="alert" className={styles.error}>{error}</p> : null}
      {notice ? <p role="status" className={styles.notice}>{notice}</p> : null}

      <ul className={styles.list}>
        {voices.map((voice) => {
          const job = jobs[voice.id];
          return (
            <li key={voice.id} className={styles.voice}>
              <div className={styles.identity}>
                <strong className={styles.name}>{voice.name}</strong>
                <span className={styles.locale}>{voice.locale}</span>
              </div>

              {selectedVoiceId === voice.id ? (
                <span className={styles.selectedBadge}>Đang chọn</span>
              ) : null}

              {voice.available ? (
                <div className={styles.actions}>
                  <button
                    type="button"
                    onClick={() => void createPreview(voice)}
                    disabled={busy || Boolean(textError)}
                    className={styles.primaryButton}
                  >
                    Nghe thử
                  </button>
                  <button
                    type="button"
                    onClick={() => void playOrRetry(voice, job, play, createPreview)}
                    className={styles.secondaryButton}
                  >
                    {playingJobId === job?.jobId ? 'Tạm dừng' : 'Phát'}
                  </button>
                  <button type="button" onClick={() => select(voice)} className={styles.secondaryButton}>
                    Chọn giọng này
                  </button>
                  <label className={styles.radio}>
                    <input
                      type="checkbox"
                      checked={compareIds.includes(voice.id)}
                      onChange={() => toggleCompare(voice.id)}
                    />
                    So sánh
                  </label>
                </div>
              ) : (
                <p className={styles.unavailable}>
                  {voice.activationHint ?? 'Cần cài model và license trước khi nghe thử.'}
                </p>
              )}

              {job ? (
                <p className={styles.jobLine}>
                  <span className={styles.status}>Trạng thái: {job.status}</span>
                  {job.fromCache ? <span className={styles.cachedBadge}>Lấy từ cache</span> : null}
                  {job.reason ? <span className={styles.error}> {job.reason}</span> : null}
                  {job.status === 'QUEUED' ? (
                    <button type="button" onClick={() => void cancelPreview(voice)} className={styles.link}>
                      Hủy
                    </button>
                  ) : null}
                  {job.status === 'FAILED' || job.status === 'CANCELLED' ? (
                    <button type="button" onClick={() => void retryPreview(voice)} className={styles.link}>
                      Thử lại
                    </button>
                  ) : null}
                </p>
              ) : null}
            </li>
          );
        })}
      </ul>

      <div className={styles.compareBox} aria-label="So sánh giọng">
        <button type="button" onClick={() => void compareSelected()} disabled={busy} className={styles.primaryButton}>
          So sánh A/B ({compareIds.length})
        </button>
        {compareJobs.length > 0 ? (
          <ul className={styles.list}>
            {compareJobs.map((job) => (
              <li key={job.jobId} className={styles.voice}>
                <span className={styles.status}>
                  {job.presetId} — {job.status}
                  {job.fromCache ? ' · cache' : ''}
                </span>
                <button type="button" onClick={() => void play(job)} className={styles.secondaryButton}>
                  {playingJobId === job.jobId ? 'Tạm dừng' : 'Phát' }
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </section>
  );
}

function playOrRetry(
  voice: VoiceOption,
  job: PreviewJobView | undefined,
  play: (job: PreviewJobView) => Promise<void>,
  createPreview: (voice: VoiceOption) => Promise<void>,
) {
  if (job && job.status === 'READY') {
    return play(job);
  }
  void createPreview(voice);
}
