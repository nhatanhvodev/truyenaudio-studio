type ProviderSettingsProps = {
  provider: string;
  model: string;
  region: string;
  policySha256: string;
  quotaLabel: string;
  committedVnd: number;
  availableVnd: number;
  onGrantFakeConsent?: () => void;
};

export default function ProviderSettings({
  provider,
  model,
  region,
  policySha256,
  quotaLabel,
  committedVnd,
  availableVnd,
  onGrantFakeConsent,
}: ProviderSettingsProps) {
  return (
    <section aria-label="Provider settings" style={styles.shell}>
      <h2 style={styles.title}>Provider settings</h2>
      <dl style={styles.facts}>
        <div>
          <dt>Provider</dt>
          <dd>{provider}</dd>
        </div>
        <div>
          <dt>Model</dt>
          <dd>{model}</dd>
        </div>
        <div>
          <dt>Region</dt>
          <dd>{region}</dd>
        </div>
        <div>
          <dt>Policy</dt>
          <dd>{policySha256.slice(0, 12)}</dd>
        </div>
        <div>
          <dt>Quota</dt>
          <dd>{quotaLabel}</dd>
        </div>
        <div>
          <dt>Budget</dt>
          <dd>{committedVnd.toLocaleString('vi-VN')} / {availableVnd.toLocaleString('vi-VN')} VND</dd>
        </div>
      </dl>
      {onGrantFakeConsent ? (
        <button type="button" onClick={onGrantFakeConsent} style={styles.button}>
          Cấp fake consent và rate card
        </button>
      ) : null}
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
    background: '#ffffff',
  },
  title: {
    margin: 0,
    fontSize: 20,
  },
  facts: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
    gap: 10,
    margin: 0,
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
