type VoiceCandidate = {
  id: string;
  provider: string;
  model: string;
  region: string;
  license: string;
  costTier: string;
  online: boolean;
  quotaLabel: string;
};

type Props = {
  sampleText: string;
  voices: VoiceCandidate[];
  consentGranted: boolean;
  onSelect: (voiceId: string) => void;
};

export default function VoiceComparison({ sampleText, voices, consentGranted, onSelect }: Props) {
  const boundedText = sampleText.trim().slice(0, 420);
  return (
    <section aria-label="Voice comparison" style={styles.shell}>
      <header>
        <h2 style={styles.title}>So sánh giọng A/B</h2>
        <p style={styles.meta}>Cùng một đoạn 20–60 giây để so sánh công bằng.</p>
      </header>
      <blockquote style={styles.sample}>{boundedText}</blockquote>
      {!consentGranted ? <p role="alert" style={styles.warning}>Chưa cấp đồng ý xử lý cloud</p> : null}
      <div style={styles.grid}>
        {voices.map((voice) => (
          <article key={voice.id} style={styles.card}>
            <strong>{voice.provider} · {voice.model}</strong>
            <span>{voice.region} · {voice.license}</span>
            <span>{voice.costTier} · {voice.online ? 'online' : 'local'}</span>
            <span>{voice.quotaLabel}</span>
            <button
              type="button"
              onClick={() => onSelect(voice.id)}
              disabled={!consentGranted && voice.online}
              style={styles.button}
            >
              Dùng {voice.provider === 'google' ? 'Google Neural2' : voice.model}
            </button>
          </article>
        ))}
      </div>
    </section>
  );
}

const styles: Record<string, React.CSSProperties> = {
  shell: {
    display: 'grid',
    gap: 12,
    padding: 16,
    border: '1px solid #d9e1e8',
    borderRadius: 10,
    background: '#fff',
  },
  title: {
    margin: 0,
    fontSize: 20,
  },
  meta: {
    margin: '4px 0 0',
    color: '#52606d',
  },
  sample: {
    margin: 0,
    padding: 12,
    borderLeft: '4px solid #1f6feb',
    background: '#f8fafc',
  },
  warning: {
    margin: 0,
    color: '#9a3412',
    fontWeight: 800,
  },
  grid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))',
    gap: 10,
  },
  card: {
    display: 'grid',
    gap: 6,
    padding: 12,
    border: '1px solid #d7dde8',
    borderRadius: 8,
  },
  button: {
    justifySelf: 'start',
    padding: '8px 12px',
    border: 0,
    borderRadius: 6,
    background: '#155eef',
    color: '#fff',
    fontWeight: 800,
  },
};
