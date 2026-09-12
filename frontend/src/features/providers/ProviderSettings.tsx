import { Button } from '../../shared/ui';

import styles from './ProviderSettings.module.css';

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
    <section aria-label="Provider settings" className={styles.shell}>
      <h2 className={styles.title}>Provider settings</h2>
      <dl className={styles.facts}>
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
        <Button variant="primary" onClick={onGrantFakeConsent}>
          Cấp fake consent và rate card
        </Button>
      ) : null}
    </section>
  );
}
