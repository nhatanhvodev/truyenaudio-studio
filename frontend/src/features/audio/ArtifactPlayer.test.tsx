import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ArtifactPlayer, { artifactContentUrl, formatTime } from './ArtifactPlayer';

afterEach(() => cleanup());

const CHAPTER_ID = 'chapter-1';
const ARTIFACT_ID = 'artifact-1';
const SHA = 'abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890';

type MediaStub = {
  play: ReturnType<typeof vi.fn>;
  pause: ReturnType<typeof vi.fn>;
  currentTime: () => number;
  dispatch: (type: string) => void;
};

function installMediaStub(element: HTMLAudioElement, duration = 61): MediaStub {
  let currentTime = 0;
  let paused = true;
  const play = vi.fn(() => {
    paused = false;
    element.dispatchEvent(new Event('play'));
    return Promise.resolve();
  });
  const pause = vi.fn(() => {
    paused = true;
    element.dispatchEvent(new Event('pause'));
  });
  Object.defineProperty(element, 'currentTime', {
    configurable: true,
    get: () => currentTime,
    set: (value: number) => {
      currentTime = value;
    },
  });
  Object.defineProperty(element, 'duration', { configurable: true, get: () => duration });
  Object.defineProperty(element, 'paused', { configurable: true, get: () => paused });
  Object.defineProperty(element, 'play', { configurable: true, value: play });
  Object.defineProperty(element, 'pause', { configurable: true, value: pause });
  return {
    play,
    pause,
    currentTime: () => currentTime,
    dispatch: (type: string) => element.dispatchEvent(new Event(type)),
  };
}

function renderPlayer(props: Partial<React.ComponentProps<typeof ArtifactPlayer>> = {}) {
  const utils = render(
    <ArtifactPlayer
      chapterId={CHAPTER_ID}
      artifactId={ARTIFACT_ID}
      sha256={SHA}
      durationMs={61_000}
      {...props}
    />,
  );
  const audio = screen.getByTestId('master-audio') as HTMLAudioElement;
  const stub = installMediaStub(audio);
  const region = screen.getByRole('region', { name: 'Nghe master' });
  return { ...utils, audio, stub, region };
}

describe('ArtifactPlayer (A05)', () => {
  it('trỏ thẳng vào URL artifact, không dùng Blob và chỉ preload metadata', () => {
    const { audio } = renderPlayer();

    expect(audio.getAttribute('src')).toBe(artifactContentUrl(CHAPTER_ID, ARTIFACT_ID));
    expect(audio.getAttribute('src')).toBe('/api/chapters/chapter-1/audio/artifacts/artifact-1/content');
    expect(audio.getAttribute('src')?.startsWith('blob:')).toBe(false);
    expect(audio.getAttribute('preload')).toBe('metadata');
  });

  it('Space phát rồi tạm dừng bằng bàn phím', async () => {
    const { stub, region } = renderPlayer();

    await act(async () => {
      fireEvent.keyDown(region, { key: ' ' });
    });
    expect(stub.play).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: 'Tạm dừng' })).toBeVisible();

    fireEvent.keyDown(region, { key: ' ' });
    expect(stub.pause).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: 'Phát' })).toBeVisible();
  });

  it('phím K cũng phát hoặc tạm dừng', async () => {
    const { stub, region } = renderPlayer();

    await act(async () => {
      fireEvent.keyDown(region, { key: 'k' });
    });
    expect(stub.play).toHaveBeenCalledTimes(1);
  });

  it('mũi tên trái/phải tua 5 giây và kẹp trong khoảng hợp lệ', () => {
    const { stub, region } = renderPlayer();

    fireEvent.keyDown(region, { key: 'ArrowRight' });
    expect(stub.currentTime()).toBe(5);

    fireEvent.keyDown(region, { key: 'ArrowRight' });
    expect(stub.currentTime()).toBe(10);

    fireEvent.keyDown(region, { key: 'ArrowLeft' });
    expect(stub.currentTime()).toBe(5);

    fireEvent.keyDown(region, { key: 'ArrowLeft' });
    fireEvent.keyDown(region, { key: 'ArrowLeft' });
    expect(stub.currentTime()).toBe(0);
  });

  it('Home và End nhảy về đầu và cuối bản master', () => {
    const { stub, region } = renderPlayer();

    fireEvent.keyDown(region, { key: 'End' });
    expect(stub.currentTime()).toBe(61);

    fireEvent.keyDown(region, { key: 'Home' });
    expect(stub.currentTime()).toBe(0);
  });

  it('PageDown/PageUp nhảy giữa các đoạn và nút Đoạn trước/Đoạn sau hoạt động', () => {
    const { stub, region } = renderPlayer({
      markers: [
        { id: 's1', label: 'Đoạn 1', seconds: 0 },
        { id: 's2', label: 'Đoạn 2', seconds: 12 },
        { id: 's3', label: 'Đoạn 3', seconds: 30 },
      ],
    });

    fireEvent.keyDown(region, { key: 'PageDown' });
    expect(stub.currentTime()).toBe(12);

    fireEvent.keyDown(region, { key: 'PageDown' });
    expect(stub.currentTime()).toBe(30);

    fireEvent.keyDown(region, { key: 'PageUp' });
    expect(stub.currentTime()).toBe(12);

    fireEvent.click(screen.getByRole('button', { name: 'Đoạn sau' }));
    expect(stub.currentTime()).toBe(30);
  });

  it('không hiển thị nút điều hướng đoạn khi không có marker', () => {
    renderPlayer();

    expect(screen.queryByRole('button', { name: 'Đoạn trước' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Đoạn sau' })).toBeNull();
  });

  it('thanh tua đổi vị trí phát và cập nhật thời gian hiển thị', () => {
    const onTimeChange = vi.fn();
    const { stub } = renderPlayer({ onTimeChange });

    fireEvent.change(screen.getByLabelText('Tua âm thanh'), { target: { value: '30' } });

    expect(stub.currentTime()).toBe(30);
    expect(screen.getByTestId('playback-position')).toHaveTextContent('0:30');
    expect(screen.getByTestId('playback-duration')).toHaveTextContent('1:01');
    expect(onTimeChange).toHaveBeenLastCalledWith(30);
  });

  it('sự kiện timeupdate của thẻ audio cập nhật vị trí đang phát', () => {
    const onTimeChange = vi.fn();
    const { stub } = renderPlayer({ onTimeChange });

    fireEvent.keyDown(screen.getByRole('region', { name: 'Nghe master' }), { key: 'ArrowRight' });
    act(() => {
      stub.dispatch('timeupdate');
    });

    expect(screen.getByTestId('playback-position')).toHaveTextContent('0:05');
    expect(onTimeChange).toHaveBeenLastCalledWith(5);
  });

  it('đọc duration thật từ loadedmetadata khi không truyền durationMs', () => {
    const { stub, region } = renderPlayer({ durationMs: null });

    act(() => {
      stub.dispatch('loadedmetadata');
    });
    fireEvent.keyDown(region, { key: 'End' });

    expect(screen.getByTestId('playback-duration')).toHaveTextContent('1:01');
    expect(stub.currentTime()).toBe(61);
  });

  it('lỗi phát hiện thành cảnh báo AUDIO_PLAYBACK_FAILED', async () => {
    const { stub, region } = renderPlayer();
    stub.play.mockImplementationOnce(() => Promise.reject(new Error('blocked')));

    await act(async () => {
      fireEvent.keyDown(region, { key: ' ' });
    });

    expect(screen.getByRole('alert')).toHaveTextContent('AUDIO_PLAYBACK_FAILED');
  });

  it('lỗi nguồn audio hiện cảnh báo AUDIO_SOURCE_UNAVAILABLE và tắt trạng thái đang phát', () => {
    const { stub } = renderPlayer();

    act(() => {
      stub.dispatch('play');
    });
    expect(screen.getByRole('button', { name: 'Tạm dừng' })).toBeVisible();

    act(() => {
      stub.dispatch('error');
    });
    expect(screen.getByRole('alert')).toHaveTextContent('AUDIO_SOURCE_UNAVAILABLE');
    expect(screen.getByRole('button', { name: 'Phát' })).toBeVisible();
  });

  it('cảnh báo khi master được thay bằng bản khác (stale nghe nhầm bản cũ)', () => {
    const { rerender } = renderPlayer();
    expect(screen.queryByRole('status')).toBeNull();

    rerender(
      <ArtifactPlayer
        chapterId={CHAPTER_ID}
        artifactId="artifact-2"
        sha256={'b'.repeat(64)}
        durationMs={61_000}
      />,
    );

    expect(screen.getByRole('status')).toHaveTextContent('Đã nạp bản master mới');
    const audio = screen.getByTestId('master-audio') as HTMLAudioElement;
    expect(audio.getAttribute('src')).toBe('/api/chapters/chapter-1/audio/artifacts/artifact-2/content');
  });

  it('hiển thị checksum rút gọn để đối chiếu bản đang nghe', () => {
    renderPlayer();

    expect(screen.getByText('checksum abcdef123456')).toBeVisible();
  });
});

describe('formatTime', () => {
  it('định dạng phút:giây và chịu được giá trị bất thường', () => {
    expect(formatTime(0)).toBe('0:00');
    expect(formatTime(5)).toBe('0:05');
    expect(formatTime(61)).toBe('1:01');
    expect(formatTime(3600)).toBe('60:00');
    expect(formatTime(Number.NaN)).toBe('0:00');
    expect(formatTime(-4)).toBe('0:00');
  });
});
