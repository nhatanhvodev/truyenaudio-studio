/*
 * NOT DEAD BY ACCIDENT — DO NOT DELETE, AND DO NOT RESTYLE.
 *
 * This component has zero importers: it is referenced nowhere under `src/`
 * except its own module and stylesheet. The `cloudConsentId` state in
 * `TranslationScreen` and `BatchQueue` is unrelated — that is a free-text box
 * where the user pastes an id by hand, not this card.
 *
 * It is not leftover. It is the frontend half of a gate the BACKEND ALREADY
 * ENFORCES:
 * - `backend/app/api/cloud_consents.py:40` serves
 *   `POST /api/projects/{project_id}/cloud-consents`, which is exactly the URL
 *   this file posts to (see `grantConsent` below) with the policy text and the
 *   accepted sha256;
 * - `backend/app/modules/compliance/cloud.py:231` refuses cloud work unless a
 *   `CloudProcessingConsent` is `GRANTED`.
 *
 * So the server will reject a paid cloud translation until a consent row
 * exists, and this card is the only UI that creates one. Deleting it would
 * remove the intended way for a user to satisfy a gate that stays switched on.
 * `docs/research/product-validation-research-v2.md:142` tracks precisely this
 * as a product GAP, naming this component and `RightsEditor` together: the
 * main route has no complete onboarding rights flow yet, so a user meets the
 * gate reason late — after doing translation/render work. Both components are
 * waiting to be wired into that flow.
 *
 * That research line names this file alongside `RightsEditor`, which DOES carry
 * a do-not-delete header. This one did not, which made it read as accidental
 * dead code to anyone doing a cleanup pass — the asymmetry was the only thing
 * protecting it, and it protected the wrong way round. Do not "fix" the
 * asymmetry in the other direction by deleting this file.
 *
 * Unlike `RightsEditor` this file is fully on ADR-0002 tokens (see
 * `CloudConsent.module.css`), so it is NOT an exception to the colour gate and
 * needs no special handling there.
 *
 * Restyling it is not the fix either: it is unreachable, so a restyle would
 * have no user-visible effect and no test could cover it. When it is finally
 * wired into the onboarding flow, convert it then — in the same change that
 * makes it reachable, where a test can see the result.
 */

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
