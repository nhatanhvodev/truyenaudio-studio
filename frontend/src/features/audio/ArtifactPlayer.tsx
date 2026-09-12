import { useCallback, useEffect, useRef, useState } from 'react';
import styles from './ArtifactPlayer.module.css';

export type AudioMarker = {
  id: string;
  label: string;
  seconds: number;
};

type Props = {
  chapterId: string;
  artifactId: string;
  sha256: string;
  durationMs?: number | null;
  markers?: AudioMarker[];
  onTimeChange?: (seconds: number) => void;
};

export const SEEK_STEP_SECONDS = 5;
const MARKER_EPSILON = 0.25;

/**
 * Player cho master audio dùng trực tiếp URL artifact (A05).
 *
 * Quan trọng: KHÔNG nạp toàn bộ master vào Blob. Thẻ <audio> trỏ thẳng vào route
 * GET /api/chapters/{id}/audio/artifacts/{artifactId}/content nên trình duyệt tự phát
 * HTTP Range request; preload="metadata" để không kéo cả file khi chỉ cần duration.
 */
export function artifactContentUrl(chapterId: string, artifactId: string): string {
  return `/api/chapters/${chapterId}/audio/artifacts/${artifactId}/content`;
}

export default function ArtifactPlayer({
  chapterId,
  artifactId,
  sha256,
  durationMs = null,
  markers = [],
  onTimeChange,
}: Props) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState(false);
  const [currentSeconds, setCurrentSeconds] = useState(0);
  const [knownDuration, setKnownDuration] = useState(durationMs ? durationMs / 1000 : 0);
  const [error, setError] = useState('');
  const [reloadNotice, setReloadNotice] = useState('');

  const url = artifactContentUrl(chapterId, artifactId);
  const orderedMarkers = [...markers].sort((left, right) => left.seconds - right.seconds);

  const loadedKeyRef = useRef<string>(`${artifactId}:${sha256}`);

  useEffect(() => {
    const nextKey = `${artifactId}:${sha256}`;
    if (loadedKeyRef.current !== nextKey) {
      loadedKeyRef.current = nextKey;
      setReloadNotice('Đã nạp bản master mới — hãy nghe lại trước khi phê duyệt.');
    }
    setCurrentSeconds(0);
    setPlaying(false);
    setKnownDuration(durationMs ? durationMs / 1000 : 0);
  }, [artifactId, sha256, durationMs]);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) {
      return;
    }

    function handleTimeUpdate() {
      const next = audio ? audio.currentTime : 0;
      setCurrentSeconds(next);
      onTimeChange?.(next);
    }
    function handleLoadedMetadata() {
      const next = audio && Number.isFinite(audio.duration) ? audio.duration : 0;
      if (next > 0) {
        setKnownDuration(next);
      }
    }
    function handlePlay() {
      setPlaying(true);
      setError('');
    }
    function handlePause() {
      setPlaying(false);
    }
    function handleEnded() {
      setPlaying(false);
    }
    function handleError() {
      setPlaying(false);
      setError('AUDIO_SOURCE_UNAVAILABLE');
    }

    audio.addEventListener('timeupdate', handleTimeUpdate);
    audio.addEventListener('loadedmetadata', handleLoadedMetadata);
    audio.addEventListener('play', handlePlay);
    audio.addEventListener('pause', handlePause);
    audio.addEventListener('ended', handleEnded);
    audio.addEventListener('error', handleError);
    return () => {
      audio.removeEventListener('timeupdate', handleTimeUpdate);
      audio.removeEventListener('loadedmetadata', handleLoadedMetadata);
      audio.removeEventListener('play', handlePlay);
      audio.removeEventListener('pause', handlePause);
      audio.removeEventListener('ended', handleEnded);
      audio.removeEventListener('error', handleError);
    };
  }, [onTimeChange]);

  const seekTo = useCallback(
    (seconds: number) => {
      const audio = audioRef.current;
      const upperBound = knownDuration > 0 ? knownDuration : Number.MAX_SAFE_INTEGER;
      const next = Math.min(Math.max(seconds, 0), upperBound);
      if (audio) {
        audio.currentTime = next;
      }
      setCurrentSeconds(next);
      onTimeChange?.(next);
    },
    [knownDuration, onTimeChange],
  );

  async function togglePlay() {
    const audio = audioRef.current;
    if (!audio) {
      return;
    }
    setError('');
    try {
      if (audio.paused) {
        await audio.play();
        setPlaying(true);
      } else {
        audio.pause();
        setPlaying(false);
      }
    } catch {
      setPlaying(false);
      setError('AUDIO_PLAYBACK_FAILED');
    }
  }

  function nextMarker(): AudioMarker | null {
    return orderedMarkers.find((marker) => marker.seconds > currentSeconds + MARKER_EPSILON) ?? null;
  }

  function previousMarker(): AudioMarker | null {
    const candidates = orderedMarkers.filter((marker) => marker.seconds < currentSeconds - MARKER_EPSILON);
    return candidates.length > 0 ? candidates[candidates.length - 1] : null;
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (event.key === ' ' || event.key === 'k' || event.key === 'K') {
      event.preventDefault();
      void togglePlay();
      return;
    }
    if (event.key === 'ArrowRight') {
      event.preventDefault();
      seekTo(currentSeconds + SEEK_STEP_SECONDS);
      return;
    }
    if (event.key === 'ArrowLeft') {
      event.preventDefault();
      seekTo(currentSeconds - SEEK_STEP_SECONDS);
      return;
    }
    if (event.key === 'Home') {
      event.preventDefault();
      seekTo(0);
      return;
    }
    if (event.key === 'End') {
      event.preventDefault();
      seekTo(knownDuration);
      return;
    }
    if (event.key === 'PageDown') {
      event.preventDefault();
      const marker = nextMarker();
      if (marker) {
        seekTo(marker.seconds);
      }
      return;
    }
    if (event.key === 'PageUp') {
      event.preventDefault();
      const marker = previousMarker();
      if (marker) {
        seekTo(marker.seconds);
      }
    }
  }

  return (
    <section
      aria-label="Nghe master"
      tabIndex={0}
      onKeyDown={handleKeyDown}
      className={styles.player}
      data-artifact-id={artifactId}
    >
      {/* controls: trình phát gốc của trình duyệt, dùng chính URL artifact ở trên (Range request). */}
      <audio
        ref={audioRef}
        src={url}
        preload="metadata"
        controls
        data-testid="master-audio"
        className={styles.audio}
      />

      <header className={styles.header}>
        <strong className={styles.heading}>Nghe master</strong>
        <span className={styles.hash}>checksum {sha256.slice(0, 12)}</span>
      </header>

      <div className={styles.row}>
        <button type="button" onClick={() => void togglePlay()} className={styles.primaryButton}>
          {playing ? 'Tạm dừng' : 'Phát'}
        </button>
        <button
          type="button"
          onClick={() => seekTo(currentSeconds - SEEK_STEP_SECONDS)}
          className={styles.secondaryButton}
        >
          Lùi 5 giây
        </button>
        <button
          type="button"
          onClick={() => seekTo(currentSeconds + SEEK_STEP_SECONDS)}
          className={styles.secondaryButton}
        >
          Tới 5 giây
        </button>
        {orderedMarkers.length > 0 ? (
          <>
            <button
              type="button"
              onClick={() => {
                const marker = previousMarker();
                if (marker) {
                  seekTo(marker.seconds);
                }
              }}
              className={styles.secondaryButton}
            >
              Đoạn trước
            </button>
            <button
              type="button"
              onClick={() => {
                const marker = nextMarker();
                if (marker) {
                  seekTo(marker.seconds);
                }
              }}
              className={styles.secondaryButton}
            >
              Đoạn sau
            </button>
          </>
        ) : null}
      </div>

      <label className={styles.scrubber}>
        <span className={styles.scrubberLabel}>Tua âm thanh</span>
        <input
          type="range"
          aria-label="Tua âm thanh"
          min={0}
          max={knownDuration || 0}
          step={0.5}
          value={currentSeconds}
          onChange={(event) => seekTo(Number(event.target.value))}
          className={styles.scrub}
        />
      </label>

      <p className={styles.time} aria-live="off">
        <span data-testid="playback-position">{formatTime(currentSeconds)}</span>
        {' / '}
        <span data-testid="playback-duration">{formatTime(knownDuration)}</span>
      </p>

      <p className={styles.hint}>
        Phím tắt: Space/K phát hoặc dừng, ←/→ tua 5 giây, PageUp/PageDown nhảy đoạn, Home/End về đầu hoặc cuối.
      </p>

      {reloadNotice ? <p role="status" className={styles.warning}>{reloadNotice}</p> : null}
      {error ? <p role="alert" className={styles.error}>{error}</p> : null}
    </section>
  );
}

export function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) {
    return '0:00';
  }
  const whole = Math.floor(seconds);
  const minutes = Math.floor(whole / 60);
  const remainder = whole % 60;
  return `${minutes}:${String(remainder).padStart(2, '0')}`;
}
