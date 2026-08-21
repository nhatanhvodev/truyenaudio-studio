import { useEffect, useState } from 'react';

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
    <section style={styles.shell} aria-label="Voice browser">
      <header style={styles.header}>
        <div>
          <h2 style={styles.title}>Voice Browser</h2>
          <p style={styles.previewText}>{data?.previewText}</p>
        </div>
      </header>

      {message ? <p role="status" style={styles.success}>{message}</p> : null}
      {error ? <p role="alert" style={styles.error}>{error}</p> : null}

      <div style={styles.list}>
        {(data?.voices ?? []).map((voice) => (
          <article key={voice.id} style={styles.voice}>
            <div style={styles.topLine}>
              <div>
                <h3 style={styles.voiceName}>{voice.name}</h3>
                <p style={styles.meta}>
                  {voice.provider} · {voice.model} · {voice.locale}
                </p>
              </div>
              <span style={voice.available ? styles.readyBadge : styles.blockedBadge}>
                {voice.available ? 'ready' : 'missing'}
              </span>
            </div>

            <dl style={styles.facts}>
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

            <p style={styles.hint}>{voice.activationHint}</p>
            <button
              type="button"
              onClick={() => void preview(voice)}
              disabled={!voice.available}
              aria-label={`Preview ${voice.name}`}
              style={voice.available ? styles.button : styles.disabledButton}
            >
              Preview
            </button>
          </article>
        ))}
      </div>
    </section>
  );
}

const styles: Record<string, React.CSSProperties> = {
  shell: {
    boxSizing: 'border-box',
    minHeight: '100vh',
    padding: 24,
    color: '#17202a',
    background: '#f6f8fb',
    fontFamily: 'Inter, Segoe UI, Arial, sans-serif',
  },
  header: {
    maxWidth: 980,
    margin: '0 auto 18px',
  },
  title: {
    margin: 0,
    fontSize: 24,
    letterSpacing: 0,
  },
  previewText: {
    maxWidth: 720,
    margin: '8px 0 0',
    color: '#52606d',
    lineHeight: 1.5,
  },
  success: {
    maxWidth: 980,
    margin: '0 auto 12px',
    color: '#166534',
    fontWeight: 700,
  },
  error: {
    maxWidth: 980,
    margin: '0 auto 12px',
    color: '#9a3412',
    fontWeight: 700,
  },
  list: {
    maxWidth: 980,
    margin: '0 auto',
    display: 'grid',
    gap: 12,
  },
  voice: {
    display: 'grid',
    gap: 12,
    padding: 16,
    border: '1px solid #d9e1e8',
    borderRadius: 8,
    background: '#ffffff',
  },
  topLine: {
    display: 'flex',
    justifyContent: 'space-between',
    gap: 16,
    alignItems: 'start',
  },
  voiceName: {
    margin: 0,
    fontSize: 18,
    letterSpacing: 0,
  },
  meta: {
    margin: '5px 0 0',
    color: '#52606d',
  },
  readyBadge: {
    padding: '5px 8px',
    borderRadius: 6,
    background: '#e8f5ec',
    color: '#1c6638',
    fontWeight: 800,
    fontSize: 13,
  },
  blockedBadge: {
    padding: '5px 8px',
    borderRadius: 6,
    background: '#fff4d6',
    color: '#7a4b00',
    fontWeight: 800,
    fontSize: 13,
  },
  facts: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))',
    gap: 10,
    margin: 0,
  },
  hint: {
    margin: 0,
    color: '#52606d',
  },
  button: {
    justifySelf: 'start',
    padding: '9px 14px',
    border: 0,
    borderRadius: 6,
    background: '#155eef',
    color: '#ffffff',
    fontWeight: 800,
  },
  disabledButton: {
    justifySelf: 'start',
    padding: '9px 14px',
    border: 0,
    borderRadius: 6,
    background: '#d0d5dd',
    color: '#475467',
    fontWeight: 800,
  },
};
