import styles from './VoiceComparison.module.css';

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
    <section aria-label="Voice comparison" className={styles.shell}>
      <header>
        <h2 className={styles.title}>So sánh giọng A/B</h2>
        <p className={styles.meta}>Cùng một đoạn 20–60 giây để so sánh công bằng.</p>
      </header>
      <blockquote className={styles.sample}>{boundedText}</blockquote>
      {!consentGranted ? <p role="alert" className={styles.error}>Chưa cấp đồng ý xử lý cloud</p> : null}
      <div className={styles.grid}>
        {voices.map((voice) => (
          <article key={voice.id} className={styles.card}>
            <strong>{voice.provider} · {voice.model}</strong>
            <span>{voice.region} · {voice.license}</span>
            <span>{voice.costTier} · {voice.online ? 'online' : 'local'}</span>
            <span>{voice.quotaLabel}</span>
            <button
              type="button"
              onClick={() => onSelect(voice.id)}
              disabled={!consentGranted && voice.online}
              className={styles.button}
            >
              Dùng {voice.provider === 'google' ? 'Google Neural2' : voice.model}
            </button>
          </article>
        ))}
      </div>
    </section>
  );
}
