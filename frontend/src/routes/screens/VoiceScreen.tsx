import { useNavigate, useParams } from 'react-router-dom';
import { useEffect, useState } from 'react';
import { apiJson } from '../../shared/api';
import VoicePreviewPanel, { type VoiceOption } from '../../features/voices/VoicePreviewPanel';
import type { RenderedAudio } from './AudioScreen';

const fakePresetId = '018f0000-0000-7000-8000-000000000001';
const fakeAudioEnabled = ((import.meta as ImportMeta & { env?: Record<string, string> }).env?.VITE_STUDIO_FAKE_AUDIO) === '1';

type VoiceCatalogPayload = {
  voices: {
    id: string;
    name: string;
    locale: string;
    available: boolean;
    active: boolean;
    activationHint?: string;
  }[];
};

export function VoiceScreen() {
  const { chapterId } = useParams();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [presetId, setPresetId] = useState(fakeAudioEnabled ? fakePresetId : '');
  const [voices, setVoices] = useState<VoiceOption[]>([]);

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
        // Nghe thử chỉ có nghĩa với giọng đã cài model + license; giọng chưa khả dụng
        // vẫn hiện trong danh sách kèm hướng dẫn, nhưng không có nút điều khiển giả (A02).
        setVoices(
          payload.voices.map((voice) => ({
            id: voice.id,
            name: voice.name,
            locale: voice.locale,
            available: voice.available,
            activationHint: voice.activationHint,
          })),
        );
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
      <VoicePreviewPanel
        voices={voices}
        selectedVoiceId={presetId || null}
        onSelect={(voiceId) => setPresetId(voiceId)}
      />
      <button type="button" onClick={() => void renderAudio()} disabled={busy || !presetId} style={styles.primaryButton}>
        Render một giọng
      </button>
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
  quote: {
    margin: 0,
    color: '#475467',
    fontWeight: 700,
  },
  title: {
    margin: 0,
    fontSize: 26,
    letterSpacing: 0,
  },
};
