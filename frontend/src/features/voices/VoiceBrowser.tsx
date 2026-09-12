import { useEffect, useState } from 'react';
import styles from './VoiceBrowser.module.css';

type Voice = {
  id: string;
  name: string;
  provider: string;
  model: string;
  locale: string;
  region: string;
  gender: string;
  license: string;
  costTier: string;
  online: boolean;
  favorite: boolean;
  available: boolean;
  active: boolean;
  activationHint: string;
};

type VoicesResponse = {
  previewText: string;
  voices: Voice[];
};

type PreviewJob = {
  id: string;
  status: string;
  kind: string;
  artifactKind: string;
  cacheKey: string;
};

export default function VoiceBrowser() {
  const [data, setData] = useState<VoicesResponse | null>(null);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;

    async function load() {
      const response = await fetch('/api/voices?locale=vi-VN');
      const payload = (await response.json()) as VoicesResponse;
      if (!cancelled) {
        setData(payload);
      }
    }

    void load().catch((reason: unknown) => {
      if (!cancelled) {
        setError(reason instanceof Error ? reason.message : 'VOICE_CATALOG_LOAD_FAILED');
      }
    });

    return () => {
      cancelled = true;
    };
  }, []);

  async function preview(voice: Voice) {
    if (!data) {
      return;
    }
    setMessage('');
    setError('');
    const response = await fetch('/api/voices/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ presetId: voice.id, text: data.previewText }),
    });
    const payload = await response.json();
    if (!response.ok) {
      setError(String(payload.detail ?? 'VOICE_PREVIEW_FAILED'));
      return;
    }
    const job = payload as PreviewJob;
    setMessage(`Preview queued ${job.id}`);
  }

  if (!data && !error) {
    return <p role="status">Dang tai danh sach giong doc</p>;
  }

  return (
    <section className={styles.shell} aria-label="Voice browser">
      <header className={styles.header}>
        <div>
          <h2 className={styles.title}>Voice Browser</h2>
          <p className={styles.previewText}>{data?.previewText}</p>
        </div>
      </header>

      {message ? <p role="status" className={styles.success}>{message}</p> : null}
      {error ? <p role="alert" className={styles.error}>{error}</p> : null}

      <div className={styles.list}>
        {(data?.voices ?? []).map((voice) => (
          <article key={voice.id} className={styles.voice}>
            <div className={styles.topLine}>
              <div>
                <h3 className={styles.voiceName}>{voice.name}</h3>
                <p className={styles.meta}>
                  {voice.provider} · {voice.model} · {voice.locale}
                </p>
              </div>
              <span className={voice.available ? styles.readyBadge : styles.blockedBadge}>
                {voice.available ? 'ready' : 'missing'}
              </span>
            </div>

            <dl className={styles.facts}>
              <div>
                <dt>Region</dt>
                <dd>{voice.region}</dd>
              </div>
              <div>
                <dt>Gender</dt>
                <dd>{voice.gender}</dd>
              </div>
              <div>
                <dt>License</dt>
                <dd>{voice.license}</dd>
              </div>
              <div>
                <dt>Cost</dt>
                <dd>{voice.costTier}</dd>
              </div>
            </dl>

            <p className={styles.hint}>{voice.activationHint}</p>
            <button
              type="button"
              onClick={() => void preview(voice)}
              disabled={!voice.available}
              aria-label={`Preview ${voice.name}`}
              className={voice.available ? styles.button : styles.disabledButton}
            >
              Preview
            </button>
          </article>
        ))}
      </div>
    </section>
  );
}
