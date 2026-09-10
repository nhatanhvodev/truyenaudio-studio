import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { QualityPlanPanel } from './QualityPlanPanel';

type Call = { url: string; init?: RequestInit };

function mockFetch(handler: (url: string, init?: RequestInit) => { status?: number; payload: unknown }) {
  const calls: Call[] = [];
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/api/security/bootstrap')) {
      return {
        ok: true,
        status: 200,
        text: async () => JSON.stringify({ csrfToken: 'test-token' }),
        json: async () => ({ csrfToken: 'test-token' }),
      };
    }
    calls.push({ url, init });
    const result = handler(url, init);
    const body = JSON.stringify(result.payload);
    return {
      ok: (result.status ?? 200) < 400,
      status: result.status ?? 200,
      text: async () => body,
      json: async () => result.payload,
    };
  });
  vi.stubGlobal('fetch', fn);
  return calls;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const baseProps = {
  chapterId: 'chapter-1',
  profileId: 'profile-1',
  cloudConsentId: 'consent-1',
  modelKey: 'qwen-mt-flash',
};

const quotePayload = {
  budgetAuthorizationId: 'auth-1',
  stage: 'TRANSLATE',
  totalVnd: 12345,
  estimateVnd: 12000,
  contingencyVnd: 345,
  expiresAt: '2026-09-09T12:00:00+00:00',
  quoteHash: 'a'.repeat(64),
  warnings: ['price snapshot 2026-09-09'],
};

describe('QualityPlanPanel (U08 part 3)', () => {
  it('defaults to Balanced with no quote and no opt-in required', () => {
    mockFetch(() => ({ payload: quotePayload }));
    render(<QualityPlanPanel {...baseProps} />);

    const mode = screen.getByLabelText('Chế độ') as HTMLSelectElement;
    expect(mode.value).toBe('BALANCED');
    expect(screen.getByRole('button', { name: 'Lấy báo giá' })).toBeEnabled();
    expect(screen.queryByLabelText('Báo giá hiện tại')).toBeNull();
  });

  it('requires explicit opt-in before quoting Quality or Maximum', async () => {
    const calls = mockFetch(() => ({ payload: quotePayload }));
    render(<QualityPlanPanel {...baseProps} />);

    fireEvent.change(screen.getByLabelText('Chế độ'), { target: { value: 'QUALITY' } });
    expect(screen.getByRole('button', { name: 'Lấy báo giá' })).toBeDisabled();
    expect(screen.getByText(/Cần bật opt-in/)).toBeTruthy();

    fireEvent.click(screen.getByLabelText(/opt-in/));
    expect(screen.getByRole('button', { name: 'Lấy báo giá' })).toBeEnabled();

    fireEvent.click(screen.getByRole('button', { name: 'Lấy báo giá' }));
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    expect(calls[0].url).toBe('/api/chapters/chapter-1/translation/quote');
    expect(JSON.parse(String(calls[0].init?.body))).toMatchObject({
      profileId: 'profile-1',
      cloudConsentId: 'consent-1',
    });
  });

  it('renders quote totals, expiry and warnings for the requested stage', async () => {
    mockFetch(() => ({ payload: quotePayload }));
    render(<QualityPlanPanel {...baseProps} />);

    fireEvent.change(screen.getByLabelText('Stage'), { target: { value: 'POLISH' } });
    fireEvent.click(screen.getByRole('button', { name: 'Lấy báo giá' }));

    const quoteBlock = await screen.findByLabelText('Báo giá hiện tại');
    expect(quoteBlock.textContent).toContain('12.345');
    expect(quoteBlock.textContent).toContain('2026');
    expect(screen.getByText(/price snapshot/)).toBeTruthy();
  });

  it('invalidates an existing quote when the model changes', async () => {
    mockFetch(() => ({ payload: quotePayload }));
    const { rerender } = render(<QualityPlanPanel {...baseProps} />);

    fireEvent.click(screen.getByRole('button', { name: 'Lấy báo giá' }));
    await screen.findByLabelText('Báo giá hiện tại');

    rerender(<QualityPlanPanel {...baseProps} modelKey="gemini-2.5-flash" />);

    await waitFor(() => expect(screen.queryByLabelText('Báo giá hiện tại')).toBeNull());
    expect(screen.getByText(/Báo giá cũ đã hết hiệu lực/)).toBeTruthy();
  });

  it('blocks quoting without cloud consent and without calling the API', async () => {
    const calls = mockFetch(() => ({ payload: quotePayload }));
    render(<QualityPlanPanel {...baseProps} cloudConsentId={null} />);

    expect(screen.getByRole('button', { name: 'Lấy báo giá' })).toBeDisabled();
    expect(screen.getByText(/Cần có cloud consent/)).toBeTruthy();
    expect(calls).toHaveLength(0);
  });

  it('surfaces blocked dispatch errors as an alert', async () => {
    mockFetch(() => ({ status: 403, payload: { detail: 'CLOUD_CONSENT_REVOKED' } }));
    render(<QualityPlanPanel {...baseProps} />);

    fireEvent.click(screen.getByRole('button', { name: 'Lấy báo giá' }));

    await waitFor(() =>
      expect(screen.getByRole('alert').textContent).toContain('CLOUD_CONSENT_REVOKED'),
    );
    expect(screen.queryByLabelText('Báo giá hiện tại')).toBeNull();
  });
});
