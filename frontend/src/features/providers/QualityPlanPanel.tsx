import { useEffect, useRef, useState } from 'react';

import { apiJson } from '../../shared/api';

export type QualityMode = 'ECONOMY' | 'BALANCED' | 'QUALITY' | 'MAXIMUM';
export type Stage = 'TRANSLATE' | 'REVIEW' | 'POLISH';

export interface QuoteView {
  budgetAuthorizationId: string;
  stage: string;
  totalVnd: number;
  estimateVnd: number;
  contingencyVnd: number;
  expiresAt: string;
  quoteHash: string | null;
  warnings?: string[];
}

export interface QualityPlanPanelProps {
  chapterId: string;
  profileId: string;
  cloudConsentId: string | null;
  /** Any change here (e.g. selected model) invalidates existing quotes. */
  modelKey: string;
}

const OPT_IN_MODES: readonly QualityMode[] = ['QUALITY', 'MAXIMUM'];

export function QualityPlanPanel({
  chapterId,
  profileId,
  cloudConsentId,
  modelKey,
}: QualityPlanPanelProps) {
  const [mode, setMode] = useState<QualityMode>('BALANCED');
  const [optIn, setOptIn] = useState(false);
  const [stage, setStage] = useState<Stage>('TRANSLATE');
  const [quote, setQuote] = useState<(QuoteView & { modelKey: string; stage: Stage }) | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const previousModelKey = useRef(modelKey);

  useEffect(() => {
    if (previousModelKey.current !== modelKey) {
      previousModelKey.current = modelKey;
      setQuote(null);
      setNotice('Báo giá cũ đã hết hiệu lực vì model/profile thay đổi. Hãy lấy báo giá mới.');
    }
  }, [modelKey]);

  const needsOptIn = OPT_IN_MODES.includes(mode);
  const optInMissing = needsOptIn && !optIn;
  const missingConsent = !cloudConsentId;
  const canQuote = !optInMissing && !missingConsent && !busy;

  async function requestQuote() {
    if (!cloudConsentId) {
      setError('CLOUD_CONSENT_REQUIRED');
      return;
    }
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const payload = await apiJson<QuoteView>(`/api/chapters/${chapterId}/translation/quote`, {
        method: 'POST',
        body: JSON.stringify({ profileId, cloudConsentId, category: stage }),
      });
      setQuote({ ...payload, modelKey, stage });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Không lấy được báo giá');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="quality-plan-heading">
      <h2 id="quality-plan-heading" style={{ margin: '0 0 8px', fontSize: 15 }}>
        Translation quality
      </h2>
      <p style={{ margin: '0 0 8px', color: '#4b5563' }}>
        Balanced là mặc định. Quality/Maximum chỉ chạy khi bạn tự bật và lấy báo giá cho từng stage.
      </p>

      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 8 }}>
        <label>
          Chế độ
          <select value={mode} onChange={(event) => setMode(event.target.value as QualityMode)}>
            <option value="ECONOMY">Economy</option>
            <option value="BALANCED">Balanced (mặc định)</option>
            <option value="QUALITY">Quality</option>
            <option value="MAXIMUM">Maximum</option>
          </select>
        </label>
        <label>
          Stage
          <select value={stage} onChange={(event) => setStage(event.target.value as Stage)}>
            <option value="TRANSLATE">Translate</option>
            <option value="REVIEW">Review</option>
            <option value="POLISH">Polish</option>
          </select>
        </label>
        <label>
          <input
            type="checkbox"
            checked={optIn}
            disabled={!needsOptIn}
            onChange={(event) => setOptIn(event.target.checked)}
          />
          Tôi chủ động bật Quality/Maximum (opt-in)
        </label>
      </div>

      {optInMissing ? (
        <p role="status">Cần bật opt-in trước khi lấy báo giá cho Quality/Maximum.</p>
      ) : null}
      {missingConsent ? (
        <p role="status">Cần có cloud consent đang hiệu lực cho profile này trước khi lấy báo giá.</p>
      ) : null}
      {error ? (
        <p role="alert" style={{ color: '#b91c1c' }}>
          {error}
        </p>
      ) : null}
      {notice ? (
        <p role="status" style={{ color: '#92400e' }}>
          {notice}
        </p>
      ) : null}

      <button type="button" disabled={!canQuote} onClick={() => void requestQuote()}>
        {busy ? 'Đang lấy báo giá…' : 'Lấy báo giá'}
      </button>

      {quote ? (
        <dl aria-label="Báo giá hiện tại">
          <dt>Stage</dt>
          <dd>{quote.stage}</dd>
          <dt>Tổng dự kiến (VND)</dt>
          <dd>{quote.totalVnd.toLocaleString('vi-VN')}</dd>
          <dt>Hết hạn</dt>
          <dd>{new Date(quote.expiresAt).toLocaleString('vi-VN')}</dd>
          {quote.warnings && quote.warnings.length > 0 ? (
            <>
              <dt>Cảnh báo</dt>
              <dd>{quote.warnings.join('; ')}</dd>
            </>
          ) : null}
        </dl>
      ) : null}
    </section>
  );
}
