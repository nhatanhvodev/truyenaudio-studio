import { useMemo, useState } from 'react';

import { Button } from '../../shared/ui';

import styles from './CloudConsent.module.css';

type CloudConsentProps = {
  projectId: string;
  providerProfileId: string;
  providerName: string;
  policyText: string;
  policySha256: string;
  dataRegion: string;
  model?: string;
  retention?: string;
  quotaLabel?: string;
  onGranted?: (consentId: string) => void;
};

type SaveState = 'idle' | 'saving' | 'granted' | 'error';

export function CloudConsent({
  projectId,
  providerProfileId,
  providerName,
  policyText,
  policySha256,
  dataRegion,
  model,
  retention = 'Theo policy snapshot đã chấp nhận',
  quotaLabel = 'Quota chưa cấu hình',
  onGranted,
}: CloudConsentProps) {
  const [accepted, setAccepted] = useState(false);
  const [state, setState] = useState<SaveState>('idle');
  const [error, setError] = useState<string | null>(null);
  const shortHash = useMemo(() => `${policySha256.slice(0, 12)}...${policySha256.slice(-8)}`, [policySha256]);

  async function grantConsent() {
    if (!accepted) {
      return;
    }
    setState('saving');
    setError(null);
    try {
      const response = await fetch(`/api/projects/${projectId}/cloud-consents`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          provider_profile_id: providerProfileId,
          policy_text: policyText,
          accepted_policy_sha256: policySha256,
          attestation_text: `${providerName} cloud processing accepted for policy ${policySha256}`,
        }),
      });
      if (!response.ok) {
        throw new Error('CONSENT_SAVE_FAILED');
      }
      const body = (await response.json()) as { id: string };
      setState('granted');
      onGranted?.(body.id);
    } catch {
      setState('error');
      setError('Khong luu duoc dong y cloud');
    }
  }

  return (
    <section aria-label="Dong y xu ly cloud" className={styles.shell}>
      <div className={styles.header}>
        <div>
          <h2 className={styles.title}>{providerName}</h2>
          <p className={styles.meta}>Policy hash {shortHash}</p>
          {model ? <p className={styles.meta}>Model {model}</p> : null}
        </div>
        <span className={styles.region}>{dataRegion}</span>
      </div>

      <p className={styles.notice}>Du lieu nguon va ban dich nhap se roi may local de xu ly tren nha cung cap nay.</p>
      <dl className={styles.facts}>
        <div>
          <dt>Retention</dt>
          <dd>{retention}</dd>
        </div>
        <div>
          <dt>Quota</dt>
          <dd>{quotaLabel}</dd>
        </div>
      </dl>

      <label className={styles.check}>
        <input type="checkbox" checked={accepted} onChange={(event) => setAccepted(event.target.checked)} />
        <span>Toi dong y rieng cho xu ly cloud cua provider nay</span>
      </label>

      <div className={styles.actions}>
        <Button
          variant="primary"
          onClick={grantConsent}
          disabled={!accepted || state === 'saving'}
        >
          {state === 'saving' ? 'Dang luu' : 'Chap nhan'}
        </Button>
        {state === 'granted' ? <span className={styles.success}>Da luu</span> : null}
        {error ? <span className={styles.error}>{error}</span> : null}
      </div>
    </section>
  );
}
