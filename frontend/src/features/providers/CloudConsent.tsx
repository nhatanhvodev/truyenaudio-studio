import { useMemo, useState } from 'react';

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
    <section aria-label="Dong y xu ly cloud" style={styles.shell}>
      <div style={styles.header}>
        <div>
          <h2 style={styles.title}>{providerName}</h2>
          <p style={styles.meta}>Policy hash {shortHash}</p>
          {model ? <p style={styles.meta}>Model {model}</p> : null}
        </div>
        <span style={styles.region}>{dataRegion}</span>
      </div>

      <p style={styles.notice}>Du lieu nguon va ban dich nhap se roi may local de xu ly tren nha cung cap nay.</p>
      <dl style={styles.facts}>
        <div>
          <dt>Retention</dt>
          <dd>{retention}</dd>
        </div>
        <div>
          <dt>Quota</dt>
          <dd>{quotaLabel}</dd>
        </div>
      </dl>

      <label style={styles.check}>
        <input type="checkbox" checked={accepted} onChange={(event) => setAccepted(event.target.checked)} />
        <span>Toi dong y rieng cho xu ly cloud cua provider nay</span>
      </label>

      <div style={styles.actions}>
        <button type="button" onClick={grantConsent} disabled={!accepted || state === 'saving'} style={styles.button}>
          {state === 'saving' ? 'Dang luu' : 'Chap nhan'}
        </button>
        {state === 'granted' ? <span style={styles.success}>Da luu</span> : null}
        {error ? <span style={styles.error}>{error}</span> : null}
      </div>
    </section>
  );
}

const styles: Record<string, React.CSSProperties> = {
  shell: {
    border: '1px solid #d9e1e8',
    borderRadius: 8,
    padding: 16,
    background: '#ffffff',
    color: '#17202a',
    fontFamily: 'Inter, Segoe UI, Arial, sans-serif',
  },
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    gap: 12,
    alignItems: 'flex-start',
    marginBottom: 12,
  },
  title: {
    margin: 0,
    fontSize: 18,
    letterSpacing: 0,
  },
  meta: {
    margin: '4px 0 0',
    color: '#52606d',
    fontSize: 13,
  },
  region: {
    border: '1px solid #a9b7c6',
    borderRadius: 6,
    padding: '4px 8px',
    color: '#17324d',
    fontSize: 13,
    fontWeight: 700,
  },
  notice: {
    margin: '0 0 14px',
    lineHeight: 1.45,
  },
  facts: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))',
    gap: 10,
    margin: '0 0 14px',
  },
  check: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    marginBottom: 14,
  },
  actions: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
  },
  button: {
    border: '1px solid #17324d',
    borderRadius: 6,
    padding: '8px 12px',
    background: '#17324d',
    color: '#ffffff',
    fontWeight: 700,
  },
  success: {
    color: '#1c6638',
    fontWeight: 700,
  },
  error: {
    color: '#8a1f11',
    fontWeight: 700,
  },
};
