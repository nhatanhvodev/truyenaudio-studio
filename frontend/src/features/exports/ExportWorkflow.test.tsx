import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ExportWorkflow, MANUAL_UPLOAD_NOTICE } from './ExportWorkflow';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const RIGHTS_HASH = 'a'.repeat(64);
const MANIFEST = 'b'.repeat(64);
const CHAPTER_ID = 'chapter-1';

const PUBLICATION_FILES = [
  'tap-0001.mp3',
  'ban-dich.md',
  'transcript.srt',
  'metadata.json',
  'production-report.json',
  'provenance.json',
  'THIRD_PARTY_LICENSES.txt',
  'checksums.sha256',
];

const PRIVATE_FILES = [
  'PRIVATE_ONLY.txt',
  'ban-dich.md',
  'source.txt',
  'production-report.json',
  'provenance.json',
  'checksums.sha256',
];

type Call = { url: string; init?: RequestInit };

type BundlePayload = {
  id: string;
  kind: string;
  status: string;
  manifestSha256: string;
  artifactId: string;
  directoryPath: string;
  files: string[];
  createdAt: string;
  verified: boolean;
  mismatches: string[];
  stale: boolean;
  staleReasons: string[];
};

function gate(allowed = true, reasons: string[] = []) {
  return { allowed, reasons, rightsEvaluationHash: RIGHTS_HASH };
}

function statusPayload(bundles: BundlePayload[] = [], allowed = true, reasons: string[] = []) {
  return { chapterId: CHAPTER_ID, gate: gate(allowed, reasons), bundles };
}

function bundle(overrides: Partial<BundlePayload> = {}): BundlePayload {
  return {
    id: 'export-1',
    kind: 'PUBLICATION_BUNDLE',
    status: 'READY',
    manifestSha256: MANIFEST,
    artifactId: 'artifact-1',
    directoryPath: 'D:/data/artifacts/exports/builds/export-1',
    files: PUBLICATION_FILES,
    createdAt: '2026-09-01T10:00:00+00:00',
    verified: true,
    mismatches: [],
    stale: false,
    staleReasons: [],
    ...overrides,
  };
}

function installFetch(handler: (url: string, init?: RequestInit) => unknown): Call[] {
  const calls: Call[] = [];
  const spy = vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    if (url === '/api/security/bootstrap') {
      return jsonResponse({ csrfToken: 'csrf-1' });
    }
    const payload = handler(url, init);
    if (isResponse(payload)) {
      return payload;
    }
    if (payload === undefined) {
      throw new Error(`unexpected url ${url}`);
    }
    return jsonResponse(payload);
  });
  vi.stubGlobal('fetch', spy);
  return calls;
}

function posts(calls: Call[], suffix: string): Call[] {
  return calls.filter((call) => call.url.endsWith(suffix) && call.init?.method === 'POST');
}

function isResponse(value: unknown): value is Response {
  return (
    typeof value === 'object' && value !== null && 'ok' in value && 'text' in value
  );
}

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return {
    ok,
    status,
    text: async () => JSON.stringify(body),
    json: async () => body,
  } as unknown as Response;
}

describe('ExportWorkflow', () => {
  it('hiển thị gate cho phép kèm hash quyền rút gọn', async () => {
    installFetch(() => statusPayload());

    render(<ExportWorkflow chapterId={CHAPTER_ID} />);

    expect(await screen.findByText('Sẵn sàng xuất bản')).toBeVisible();
    expect(screen.getByTestId('rights-hash')).toHaveTextContent('aaaaaaaaaaaa…');
    expect(screen.getByRole('button', { name: 'Tạo bundle publication' })).toBeEnabled();
  });

  it('gate chặn thì nút publication bị vô hiệu hoá và không gọi API', async () => {
    const calls = installFetch(() => statusPayload([], false, ['PUBLIC_STREAM']));

    render(<ExportWorkflow chapterId={CHAPTER_ID} />);

    expect(await screen.findByText('Bị chặn xuất bản')).toBeVisible();
    expect(screen.getByText('PUBLIC_STREAM')).toBeVisible();

    const publication = screen.getByRole('button', { name: 'Tạo bundle publication' });
    expect(publication).toBeDisabled();
    fireEvent.click(publication);

    await waitFor(() => expect(posts(calls, '/exports/publication')).toHaveLength(0));
    expect(screen.getByRole('button', { name: 'Tạo archive riêng tư' })).toBeEnabled();
  });

  it('tiêu đề tập rỗng bị chặn tại client trước khi gọi API', async () => {
    const calls = installFetch(() => statusPayload());
    render(<ExportWorkflow chapterId={CHAPTER_ID} />);
    await screen.findByText('Sẵn sàng xuất bản');

    fireEvent.click(screen.getByRole('button', { name: 'Tạo bundle publication' }));

    expect(await screen.findByText(/EPISODE_TITLE_REQUIRED/)).toBeVisible();
    expect(calls.some((call) => call.url.endsWith('/exports/publication'))).toBe(false);
    expect(calls.some((call) => call.url === '/api/security/bootstrap')).toBe(false);
  });

  it('số tập nhỏ hơn 1 bị chặn tại client trước khi gọi API', async () => {
    const calls = installFetch(() => statusPayload());
    render(<ExportWorkflow chapterId={CHAPTER_ID} />);
    await screen.findByText('Sẵn sàng xuất bản');

    fireEvent.change(screen.getByLabelText('Tiêu đề tập'), { target: { value: 'Tập một' } });
    fireEvent.change(screen.getByLabelText('Số tập'), { target: { value: '0' } });
    fireEvent.click(screen.getByRole('button', { name: 'Tạo bundle publication' }));

    expect(await screen.findByText(/EPISODE_NUMBER_POSITIVE_REQUIRED/)).toBeVisible();
    expect(calls.some((call) => call.url.endsWith('/exports/publication'))).toBe(false);
  });

  it('tạo archive riêng tư rồi hiển thị manifest, nhóm file và trạng thái checksum', async () => {
    let built = false;
    const calls = installFetch((url, init) => {
      if (url.endsWith('/exports/status')) {
        return statusPayload(
          built ? [bundle({ id: 'export-private-1', kind: 'PRIVATE_ARCHIVE', files: PRIVATE_FILES })] : [],
        );
      }
      if (url.endsWith('/exports/private') && init?.method === 'POST') {
        built = true;
        return {
          id: 'export-private-1',
          kind: 'PRIVATE_ARCHIVE',
          manifestSha256: MANIFEST,
          files: PRIVATE_FILES,
        };
      }
      return undefined;
    });

    render(<ExportWorkflow chapterId={CHAPTER_ID} />);
    await screen.findByText('Sẵn sàng xuất bản');

    fireEvent.click(screen.getByRole('button', { name: 'Tạo archive riêng tư' }));

    expect(await screen.findByText('Đã tạo archive riêng tư.')).toBeVisible();
    const [privatePost] = posts(calls, '/exports/private');
    expect(JSON.parse(String(privatePost.init?.body))).toEqual({});

    const card = await screen.findByRole('article', { name: 'Archive riêng tư' });
    expect(within(card).getByText(MANIFEST)).toBeVisible();
    expect(within(card).getByText('Checksum khớp')).toBeVisible();
    expect(within(card).getByRole('heading', { name: 'Checksum' })).toBeVisible();
    expect(within(card).getByText('checksums.sha256')).toBeVisible();
    expect(within(card).getByRole('heading', { name: 'Provenance' })).toBeVisible();
    expect(within(card).getByText('provenance.json')).toBeVisible();
    expect(within(card).queryByText('tap-0001.mp3')).toBeNull();
  });

  it('bundle bị lệch checksum thì hiện danh sách mismatches', async () => {
    installFetch(() =>
      statusPayload([
        bundle({ verified: false, mismatches: ['tap-0001.mp3', 'transcript.srt'] }),
      ]),
    );

    render(<ExportWorkflow chapterId={CHAPTER_ID} />);

    expect(await screen.findByText('Checksum lệch')).toBeVisible();
    const alert = screen.getByText('File lệch hoặc thiếu checksum:').closest('[role="alert"]');
    expect(alert).not.toBeNull();
    expect(within(alert as HTMLElement).getByText('tap-0001.mp3')).toBeVisible();
    expect(within(alert as HTMLElement).getByText('transcript.srt')).toBeVisible();
  });

  it('bundle stale thì cảnh báo kèm lý do và cho phép tạo lại', async () => {
    let built = false;
    const calls = installFetch((url, init) => {
      if (url.endsWith('/exports/status')) {
        return statusPayload([
          built
            ? bundle({ verified: true, stale: false, staleReasons: [] })
            : bundle({ stale: true, staleReasons: ['RIGHTS_CHANGED', 'MASTER_CHANGED'] }),
        ]);
      }
      if (url.endsWith('/exports/publication') && init?.method === 'POST') {
        built = true;
        return { id: 'export-1', kind: 'PUBLICATION_BUNDLE', manifestSha256: MANIFEST, files: PUBLICATION_FILES };
      }
      return undefined;
    });

    render(<ExportWorkflow chapterId={CHAPTER_ID} />);
    await screen.findByText('Sẵn sàng xuất bản');

    const warning = await screen.findByText('Bản xuất đã cũ (STALE) — hãy tạo lại trước khi dùng.');
    expect(warning).toBeVisible();
    expect(screen.getByText(/RIGHTS_CHANGED/)).toBeVisible();
    expect(screen.getByText(/MASTER_CHANGED/)).toBeVisible();

    fireEvent.change(screen.getByLabelText('Tiêu đề tập'), { target: { value: 'Tập một' } });
    fireEvent.click(screen.getByRole('button', { name: 'Tạo lại bundle' }));

    await waitFor(() => expect(posts(calls, '/exports/publication')).toHaveLength(1));
    await waitFor(() =>
      expect(screen.queryByText('Bản xuất đã cũ (STALE) — hãy tạo lại trước khi dùng.')).toBeNull(),
    );
  });

  it('luôn nhắc rằng studio không tự upload bản xuất', async () => {
    const calls = installFetch(() => statusPayload([bundle()]));

    render(<ExportWorkflow chapterId={CHAPTER_ID} />);

    expect(await screen.findByText(MANUAL_UPLOAD_NOTICE)).toBeVisible();
    expect(
      calls.some((call) => /upload|publish-to|cloud/i.test(call.url)),
    ).toBe(false);
  });

  it('gửi metadata publication đúng hợp đồng camelCase', async () => {
    const calls = installFetch((url, init) => {
      if (url.endsWith('/exports/status')) {
        return statusPayload([]);
      }
      if (url.endsWith('/exports/publication') && init?.method === 'POST') {
        return { id: 'export-1', kind: 'PUBLICATION_BUNDLE', manifestSha256: MANIFEST, files: PUBLICATION_FILES };
      }
      return undefined;
    });

    render(<ExportWorkflow chapterId={CHAPTER_ID} />);
    await screen.findByText('Sẵn sàng xuất bản');
    fireEvent.change(screen.getByLabelText('Tiêu đề tập'), { target: { value: 'Tập bảy' } });
    fireEvent.change(screen.getByLabelText('Số tập'), { target: { value: '7' } });
    fireEvent.click(screen.getByLabelText('Tập premium'));
    fireEvent.click(screen.getByRole('button', { name: 'Tạo bundle publication' }));

    await waitFor(() => expect(posts(calls, '/exports/publication')).toHaveLength(1));
    const [publicationPost] = posts(calls, '/exports/publication');
    expect(JSON.parse(String(publicationPost.init?.body))).toEqual({
      episodeTitle: 'Tập bảy',
      suggestedEpisodeNumber: 7,
      isPremium: true,
    });
  });

  it('không ghi bất cứ thứ gì vào localStorage', async () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem');
    installFetch((url, init) => {
      if (url.endsWith('/exports/status')) {
        return statusPayload([bundle()]);
      }
      if (url.endsWith('/exports/private') && init?.method === 'POST') {
        return { id: 'export-private-1', kind: 'PRIVATE_ARCHIVE', manifestSha256: MANIFEST, files: PRIVATE_FILES };
      }
      return undefined;
    });

    render(<ExportWorkflow chapterId={CHAPTER_ID} />);
    await screen.findByText('Sẵn sàng xuất bản');
    fireEvent.click(screen.getByRole('button', { name: 'Tạo archive riêng tư' }));
    await screen.findByText('Đã tạo archive riêng tư.');

    expect(setItem).not.toHaveBeenCalled();
    expect(window.localStorage.length).toBe(0);
  });

  it('lỗi tải trạng thái export thì hiện cảnh báo thay vì im lặng', async () => {
    installFetch((url) => {
      if (url.endsWith('/exports/status')) {
        return jsonResponse({ detail: 'EXPORT_STATUS_FAILED' }, false, 500);
      }
      return undefined;
    });

    render(<ExportWorkflow chapterId={CHAPTER_ID} />);

    expect(
      await screen.findByText('Không tải được trạng thái export: EXPORT_STATUS_FAILED'),
    ).toBeVisible();
  });
});
