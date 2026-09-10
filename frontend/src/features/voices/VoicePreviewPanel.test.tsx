import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import VoicePreviewPanel, { DEFAULT_SAMPLE_TEXT, PREVIEW_TEXT_LIMIT, type VoiceOption } from './VoicePreviewPanel';

const VOICES: VoiceOption[] = [
  { id: 'voice-a', name: 'Giọng A', locale: 'vi-VN', available: true },
  { id: 'voice-b', name: 'Giọng B', locale: 'vi-VN', available: true },
  { id: 'voice-c', name: 'Giọng C', locale: 'vi-VN', available: false, activationHint: 'Cần cài model VieNeu và license.' },
];

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status < 400,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response;
}

function job(overrides: Record<string, unknown> = {}) {
  return {
    jobId: 'job-1',
    presetId: 'voice-a',
    textKind: 'SAMPLE',
    status: 'READY',
    cacheKey: 'cache-1',
    fromCache: false,
    audioUrl: null,
    durationMs: 1200,
    reason: null,
    ...overrides,
  };
}

type Call = { url: string; init?: RequestInit };

function stubFetch(handler: (url: string, init?: RequestInit) => Response): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      return handler(url, init);
    }),
  );
  return calls;
}

function installAudioStub() {
  const audio = screen.getByTestId('preview-audio') as HTMLAudioElement;
  let playing = false;
  const play = vi.fn(() => {
    playing = true;
    return Promise.resolve();
  });
  const pause = vi.fn(() => {
    playing = false;
  });
  Object.defineProperty(audio, 'play', { configurable: true, value: play });
  Object.defineProperty(audio, 'pause', { configurable: true, value: pause });
  Object.defineProperty(audio, 'paused', { configurable: true, get: () => !playing });
  return { audio, play, pause };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('VoicePreviewPanel (A02)', () => {
  it('nghe thử bằng văn bản mẫu và gửi đúng preset + textKind', async () => {
    const calls = stubFetch(() => jsonResponse(job()));
    render(<VoicePreviewPanel voices={VOICES} />);

    expect(screen.getByText(DEFAULT_SAMPLE_TEXT)).toBeVisible();
    fireEvent.click(within(screen.getByText('Giọng A').closest('li') as HTMLElement).getByRole('button', { name: 'Nghe thử' }));

    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].url).toBe('/api/voices/preview-jobs');
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({
      presetId: 'voice-a',
      text: DEFAULT_SAMPLE_TEXT,
      textKind: 'SAMPLE',
    });
  });

  it('đếm ký tự văn bản tự nhập và chặn khi vượt 420 ký tự', () => {
    const calls = stubFetch(() => jsonResponse(job()));
    render(<VoicePreviewPanel voices={VOICES} />);

    fireEvent.click(screen.getByLabelText('Văn bản tự nhập'));
    fireEvent.change(screen.getByLabelText('Văn bản nghe thử'), { target: { value: 'a'.repeat(421) } });

    expect(screen.getByText('421/420 ký tự')).toBeVisible();
    const previewButton = within(screen.getByText('Giọng A').closest('li') as HTMLElement).getByRole('button', {
      name: 'Nghe thử',
    });
    expect(previewButton).toBeDisabled();
    expect(calls).toHaveLength(0);
  });

  it('chấp nhận đúng 420 ký tự', async () => {
    const calls = stubFetch(() => jsonResponse(job({ textKind: 'CUSTOM' })));
    render(<VoicePreviewPanel voices={VOICES} />);

    fireEvent.click(screen.getByLabelText('Văn bản tự nhập'));
    fireEvent.change(screen.getByLabelText('Văn bản nghe thử'), { target: { value: 'b'.repeat(PREVIEW_TEXT_LIMIT) } });
    fireEvent.click(within(screen.getByText('Giọng A').closest('li') as HTMLElement).getByRole('button', { name: 'Nghe thử' }));

    await waitFor(() => expect(calls).toHaveLength(1));
  });

  it('không gọi API khi văn bản tự nhập rỗng', () => {
    const calls = stubFetch(() => jsonResponse(job()));
    render(<VoicePreviewPanel voices={VOICES} />);

    fireEvent.click(screen.getByLabelText('Văn bản tự nhập'));
    fireEvent.change(screen.getByLabelText('Văn bản nghe thử'), { target: { value: '   ' } });

    const previewButton = within(screen.getByText('Giọng A').closest('li') as HTMLElement).getByRole('button', {
      name: 'Nghe thử',
    });
    expect(previewButton).toBeDisabled();
    expect(calls).toHaveLength(0);
  });

  it('phát bản nghe thử qua URL job và chỉ có một player dùng chung', async () => {
    stubFetch((url, init) => {
      const requested = JSON.parse(String(init?.body ?? '{}')).presetId as string | undefined;
      const presetId = requested ?? (url.includes('job-b') ? 'voice-b' : 'voice-a');
      return jsonResponse(job({ jobId: `job-${presetId}`, presetId }));
    });
    render(<VoicePreviewPanel voices={VOICES} />);
    const audio = installAudioStub();

    const rowA = screen.getByText('Giọng A').closest('li') as HTMLElement;
    fireEvent.click(within(rowA).getByRole('button', { name: 'Nghe thử' }));
    await waitFor(() => expect(within(rowA).getByRole('button', { name: 'Phát' })).toBeEnabled());
    fireEvent.click(within(rowA).getByRole('button', { name: 'Phát' }));

    await waitFor(() => expect(audio.play).toHaveBeenCalledTimes(1));
    expect(audio.audio.getAttribute('src')).toBe('/api/voices/preview-jobs/job-voice-a/content');

    const rowB = screen.getByText('Giọng B').closest('li') as HTMLElement;
    fireEvent.click(within(rowB).getByRole('button', { name: 'Nghe thử' }));
    await waitFor(() => expect(within(rowB).getByRole('button', { name: 'Phát' })).toBeEnabled());
    fireEvent.click(within(rowB).getByRole('button', { name: 'Phát' }));

    await waitFor(() => expect(audio.play).toHaveBeenCalledTimes(2));
    expect(audio.audio.getAttribute('src')).toBe('/api/voices/preview-jobs/job-voice-b/content');
    expect(screen.getAllByTestId('preview-audio')).toHaveLength(1);
  });

  it('hiển thị nhãn lấy từ cache khi backend trả fromCache', async () => {
    stubFetch(() => jsonResponse(job({ fromCache: true })));
    render(<VoicePreviewPanel voices={VOICES} />);

    const row = screen.getByText('Giọng A').closest('li') as HTMLElement;
    fireEvent.click(within(row).getByRole('button', { name: 'Nghe thử' }));

    expect(await within(row).findByText('Lấy từ cache')).toBeVisible();
  });

  it('ẩn điều khiển không hỗ trợ với giọng chưa khả dụng', () => {
    stubFetch(() => jsonResponse(job()));
    render(<VoicePreviewPanel voices={VOICES} />);

    const row = screen.getByText('Giọng C').closest('li') as HTMLElement;
    expect(within(row).queryByRole('button', { name: 'Nghe thử' })).toBeNull();
    expect(within(row).getByText('Cần cài model VieNeu và license.')).toBeVisible();
  });

  it('chọn giọng trả về đúng voice ID và giữ nhãn đang chọn', () => {
    stubFetch(() => jsonResponse(job()));
    const onSelect = vi.fn();
    render(<VoicePreviewPanel voices={VOICES} selectedVoiceId="voice-b" onSelect={onSelect} />);

    const rowB = screen.getByText('Giọng B').closest('li') as HTMLElement;
    expect(within(rowB).getByText('Đang chọn')).toBeVisible();

    const rowA = screen.getByText('Giọng A').closest('li') as HTMLElement;
    fireEvent.click(within(rowA).getByRole('button', { name: 'Chọn giọng này' }));

    expect(onSelect).toHaveBeenCalledWith('voice-a');
    expect(screen.getByText('Đã chọn giọng Giọng A')).toBeVisible();
  });

  it('theo dõi job đang xếp hàng cho tới khi READY', async () => {
    let polls = 0;
    stubFetch((url) => {
      if (url === '/api/voices/preview-jobs') {
        return jsonResponse(job({ status: 'QUEUED' }));
      }
      polls += 1;
      return jsonResponse(job({ status: polls >= 2 ? 'READY' : 'QUEUED' }));
    });
    render(<VoicePreviewPanel voices={VOICES} />);

    const row = screen.getByText('Giọng A').closest('li') as HTMLElement;
    fireEvent.click(within(row).getByRole('button', { name: 'Nghe thử' }));
    expect(await within(row).findByText(/Trạng thái: QUEUED/)).toBeVisible();

    await waitFor(() => expect(within(row).getByText(/Trạng thái: READY/)).toBeVisible(), {
      timeout: 4000,
    });
  });

  it('hủy job đang xếp hàng', async () => {
    const calls = stubFetch((url) =>
      url.endsWith('/cancel')
        ? jsonResponse(job({ status: 'CANCELLED' }))
        : jsonResponse(job({ status: 'QUEUED' })),
    );
    render(<VoicePreviewPanel voices={VOICES} />);

    const row = screen.getByText('Giọng A').closest('li') as HTMLElement;
    fireEvent.click(within(row).getByRole('button', { name: 'Nghe thử' }));
    fireEvent.click(await within(row).findByRole('button', { name: 'Hủy' }));

    await waitFor(() => expect(within(row).getByText(/Trạng thái: CANCELLED/)).toBeVisible());
    expect(calls.some((call) => call.url === '/api/voices/preview-jobs/job-1/cancel')).toBe(true);
  });

  it('cho thử lại job lỗi và giữ reason của backend', async () => {
    const calls = stubFetch((url) =>
      url.endsWith('/retry')
        ? jsonResponse(job({ status: 'READY' }))
        : jsonResponse(job({ status: 'FAILED', reason: 'VOICE_MODEL_UNAVAILABLE' })),
    );
    render(<VoicePreviewPanel voices={VOICES} />);

    const row = screen.getByText('Giọng A').closest('li') as HTMLElement;
    fireEvent.click(within(row).getByRole('button', { name: 'Nghe thử' }));

    expect(await within(row).findByText('VOICE_MODEL_UNAVAILABLE')).toBeVisible();
    fireEvent.click(within(row).getByRole('button', { name: 'Thử lại' }));

    await waitFor(() => expect(within(row).getByText(/Trạng thái: READY/)).toBeVisible());
    expect(calls.some((call) => call.url === '/api/voices/preview-jobs/job-1/retry')).toBe(true);
  });

  it('so sánh A/B cần ít nhất hai giọng', async () => {
    const calls = stubFetch(() => jsonResponse(job()));
    render(<VoicePreviewPanel voices={VOICES} />);

    fireEvent.click(screen.getByRole('button', { name: 'So sánh A/B (0)' }));

    expect(screen.getByRole('alert')).toHaveTextContent('PREVIEW_COMPARE_REQUIRES_TWO');
    expect(calls).toHaveLength(0);
  });

  it('so sánh A/B gửi cùng một văn bản và phát trên cùng player', async () => {
    const calls = stubFetch((url) => {
      if (url === '/api/voices/preview-jobs/compare') {
        return jsonResponse({
          text: DEFAULT_SAMPLE_TEXT,
          textSha256: 'same-hash',
          jobs: [
            job({ jobId: 'job-a', presetId: 'voice-a' }),
            job({ jobId: 'job-b', presetId: 'voice-b' }),
          ],
        });
      }
      return jsonResponse(job());
    });
    render(<VoicePreviewPanel voices={VOICES} />);
    const audio = installAudioStub();

    const compareBoxes = screen.getAllByLabelText('So sánh');
    fireEvent.click(compareBoxes[0]);
    fireEvent.click(compareBoxes[1]);
    fireEvent.click(screen.getByRole('button', { name: 'So sánh A/B (2)' }));

    await waitFor(() => expect(screen.getByText(/voice-a — READY/)).toBeVisible());
    expect(screen.getByText(/voice-b — READY/)).toBeVisible();

    const compareCall = calls.find((call) => call.url === '/api/voices/preview-jobs/compare');
    expect(JSON.parse(String(compareCall?.init?.body))).toEqual({
      presetIds: ['voice-a', 'voice-b'],
      text: DEFAULT_SAMPLE_TEXT,
    });

    const compareArea = screen.getByLabelText('So sánh giọng');
    fireEvent.click(within(compareArea).getAllByRole('button', { name: 'Phát' })[0]);
    await waitFor(() => expect(audio.play).toHaveBeenCalledTimes(1));
    expect(audio.audio.getAttribute('src')).toBe('/api/voices/preview-jobs/job-a/content');
  });

  it('hiện mã lỗi backend khi tạo bản nghe thử thất bại', async () => {
    stubFetch(() => jsonResponse({ detail: 'VOICE_PRESET_NOT_FOUND' }, 404));
    render(<VoicePreviewPanel voices={VOICES} />);

    const row = screen.getByText('Giọng A').closest('li') as HTMLElement;
    fireEvent.click(within(row).getByRole('button', { name: 'Nghe thử' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('VOICE_PRESET_NOT_FOUND');
  });
});
