import { useCallback, useEffect, useState } from 'react';

import { apiJson } from '../../shared/api';

export interface ModelSnapshotView {
  providerId: string;
  modelId: string;
  apiKind: string;
  contextTokens: number | null;
  maxOutputTokens?: number | null;
  languages: string[];
  capabilities?: { stream?: string; structured?: string; translation?: string };
  availability: string;
  pricing: { class: string; currency?: string | null };
  license?: string | null;
  sourceUrl: string;
  fetchedAt: string;
  expiresAt: string;
  benchmarkRef?: string | null;
}

interface ModelsResponse {
  items: ModelSnapshotView[];
  nextCursor?: string | null;
  stale?: boolean;
}

const PAGE_LIMIT = 100; // server maximum mounted rows per page

export interface ModelCatalogProps {
  profileId?: string;
  providerId?: string;
}

function pricingLabel(pricingClass: string): string {
  if (pricingClass === 'free') {
    return 'Miễn phí';
  }
  if (pricingClass === 'paid') {
    return 'Trả phí';
  }
  return 'Chưa rõ giá';
}

function availabilityLabel(availability: string): string {
  if (availability === 'available') {
    return 'Khả dụng';
  }
  if (availability === 'unavailable') {
    return 'Không khả dụng';
  }
  return 'Chưa xác minh';
}

export function buildModelsQuery(
  filters: { pricing: string; contextMin: string; benchmarked: boolean },
  cursor?: string | null,
): string {
  const params = new URLSearchParams();
  params.set('limit', String(PAGE_LIMIT));
  if (cursor) {
    params.set('cursor', cursor);
  }
  if (filters.pricing !== 'all') {
    params.set('pricing', filters.pricing);
  }
  const contextMin = Number.parseInt(filters.contextMin, 10);
  if (Number.isFinite(contextMin) && contextMin > 0) {
    params.set('contextMin', String(contextMin));
  }
  if (filters.benchmarked) {
    params.set('benchmarked', 'true');
  }
  return params.toString();
}

export function ModelCatalog({ profileId, providerId }: ModelCatalogProps) {
  const [pricing, setPricing] = useState('all');
  const [contextMin, setContextMin] = useState('');
  const [benchmarked, setBenchmarked] = useState(false);
  const [items, setItems] = useState<ModelSnapshotView[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(
    async (nextCursor?: string | null) => {
      setLoading(true);
      setError('');
      try {
        const query = buildModelsQuery({ pricing, contextMin, benchmarked }, nextCursor);
        const suffix = profileId ? `&profileId=${encodeURIComponent(profileId)}` : '';
        const providerSuffix = providerId ? `&providerId=${encodeURIComponent(providerId)}` : '';
        const payload = await apiJson<ModelsResponse>(`/api/models?${query}${suffix}${providerSuffix}`);
        setItems((previous) => (nextCursor ? [...previous, ...payload.items] : payload.items));
        setCursor(payload.nextCursor ?? null);
        setStale(Boolean(payload.stale));
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : 'Không tải được catalog');
      } finally {
        setLoading(false);
      }
    },
    [benchmarked, contextMin, pricing, profileId, providerId],
  );

  useEffect(() => {
    void load(null);
  }, [load]);

  return (
    <section aria-labelledby="model-catalog-heading">
      <h2 id="model-catalog-heading" style={{ margin: '0 0 8px', fontSize: 15 }}>
        Models
      </h2>
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
        <label>
          Giá
          <select value={pricing} onChange={(event) => setPricing(event.target.value)}>
            <option value="all">Tất cả</option>
            <option value="free">Miễn phí</option>
            <option value="paid">Trả phí</option>
          </select>
        </label>
        <label>
          Context tối thiểu
          <input
            inputMode="numeric"
            value={contextMin}
            onChange={(event) => setContextMin(event.target.value.replace(/[^0-9]/g, ''))}
            style={{ width: 90 }}
          />
        </label>
        <label>
          <input
            type="checkbox"
            checked={benchmarked}
            onChange={(event) => setBenchmarked(event.target.checked)}
          />
          Chỉ model có benchmark
        </label>
      </div>

      {stale ? (
        <p role="status" style={{ color: '#92400e' }}>
          Dữ liệu catalog có thể đã cũ; kiểm tra nguồn và ngày trước khi chọn model.
        </p>
      ) : null}
      {error ? (
        <p role="alert" style={{ color: '#b91c1c' }}>
          {error}
        </p>
      ) : null}

      <table>
        <caption>Model khả dụng theo filter hiện tại</caption>
        <thead>
          <tr>
            <th scope="col">Model</th>
            <th scope="col">API</th>
            <th scope="col">Context</th>
            <th scope="col">Giá</th>
            <th scope="col">Trạng thái</th>
            <th scope="col">Nguồn</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={`${item.providerId}:${item.modelId}`}>
              <td>{item.modelId}</td>
              <td>{item.apiKind}</td>
              <td>{item.contextTokens ?? 'không rõ'}</td>
              <td>{pricingLabel(item.pricing?.class ?? 'unknown')}</td>
              <td>{availabilityLabel(item.availability)}</td>
              <td>
                <a href={item.sourceUrl} rel="noreferrer noopener" target="_blank">
                  {item.providerId}
                </a>
                {item.benchmarkRef ? <span> · benchmark có tham chiếu</span> : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {items.length === 0 && !loading ? <p>Không có model khớp filter.</p> : null}

      <button type="button" disabled={loading || !cursor} onClick={() => void load(cursor)}>
        {loading ? 'Đang tải…' : cursor ? 'Tải thêm' : 'Hết danh sách'}
      </button>
    </section>
  );
}
