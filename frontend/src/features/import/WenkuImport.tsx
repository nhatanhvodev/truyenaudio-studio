import { useState, useEffect, useRef } from 'react';
import { apiJson } from '../../shared/api';
import ImportPreview, { type ImportCandidate } from './ImportPreview';
import { useWenkuCrawl } from './WenkuCrawlContext';

import styles from './WenkuImport.module.css';

type CatalogItem = {
  ordinal: number;
  title: string;
  url: string;
  isAppFree: boolean;
};

export type WenkuNovelInfo = {
  bookId: string;
  title: string;
  author: string;
  category: string;
  status: string;
  description: string;
  coverUrl: string;
  latestChapter: string;
  wordCountText: string;
  totalChapters: number;
  catalog: CatalogItem[];
  sourceUrl: string;
};

type RankItem = {
  bookId: string;
  title: string;
  url: string;
};

type Props = {
  projectId: string;
  onImportSuccess: (firstChapterId: string) => void;
};

const RANK_TABS = [
  { id: 'hot', label: 'Bảng Đang Hot (热门)' },
  { id: 'free', label: 'Miễn Phí (免费)' },
  { id: 'monthly', label: 'Nguyệt Phiếu (月票)' },
  { id: 'soaring', label: 'Tăng Trưởng (飙升)' },
  { id: 'god', label: 'Phong Thần (封神)' },
];

// Ước tính ~1.5 giây/chương khi cào
const SECS_PER_CHAPTER = 1.5;
// Giới hạn text lưu localStorage để tránh vượt quota 5MB
const MAX_TEXT_PER_CHAPTER = 4000;

type DraftData = {
  novelInfo: WenkuNovelInfo;
  candidates: ImportCandidate[];
  startChapter: number;
  endChapter: number;
  savedAt: string;
};

function getDraftKey(projectId: string) {
  return `wenku_draft_${projectId}`;
}

function saveDraft(projectId: string, data: DraftData) {
  try {
    const payload: DraftData = {
      ...data,
      candidates: data.candidates.map((c) => ({
        ...c,
        text: c.text.slice(0, MAX_TEXT_PER_CHAPTER),
      })),
    };
    localStorage.setItem(getDraftKey(projectId), JSON.stringify(payload));
  } catch {
    // Quota vượt giới hạn — bỏ qua lỗi
  }
}

function loadDraft(projectId: string): DraftData | null {
  try {
    const raw = localStorage.getItem(getDraftKey(projectId));
    return raw ? (JSON.parse(raw) as DraftData) : null;
  } catch {
    return null;
  }
}

function clearDraft(projectId: string) {
  try {
    localStorage.removeItem(getDraftKey(projectId));
  } catch {
    // ignore
  }
}

// The crawl animations (shimmer / pulse / spin) used to be injected into <head>
// from JS; they now live in WenkuImport.module.css as ordinary classes, so the
// stylesheet carries the keyframes and every colour comes from a token.

export function WenkuImport({ projectId, onImportSuccess }: Props) {
  const { setCrawlState } = useWenkuCrawl();

  const [urlOrId, setUrlOrId] = useState('');
  const [novelInfo, setNovelInfo] = useState<WenkuNovelInfo | null>(null);
  const [startChapter, setStartChapter] = useState(1);
  const [endChapter, setEndChapter] = useState(10);
  const [candidates, setCandidates] = useState<ImportCandidate[]>([]);

  const [activeRank, setActiveRank] = useState('hot');
  const [rankings, setRankings] = useState<RankItem[]>([]);
  const [showRankings, setShowRankings] = useState(false);

  const [loadingInfo, setLoadingInfo] = useState(false);
  const [loadingRank, setLoadingRank] = useState(false);
  const [crawling, setCrawling] = useState(false);
  const [crawlElapsed, setCrawlElapsed] = useState(0);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState('');
  const [draftBanner, setDraftBanner] = useState<DraftData | null>(null);

  const crawlTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Khôi phục draft từ localStorage khi mount
  useEffect(() => {
    const draft = loadDraft(projectId);
    if (draft && draft.candidates.length > 0) {
      setDraftBanner(draft);
    }
  }, [projectId]);

  // Timer đếm giây khi đang cào
  useEffect(() => {
    if (crawling) {
      setCrawlElapsed(0);
      crawlTimerRef.current = setInterval(() => {
        setCrawlElapsed((prev) => prev + 1);
      }, 1000);
    } else {
      if (crawlTimerRef.current) {
        clearInterval(crawlTimerRef.current);
        crawlTimerRef.current = null;
      }
    }
    return () => {
      if (crawlTimerRef.current) clearInterval(crawlTimerRef.current);
    };
  }, [crawling]);

  function applyDraft(draft: DraftData) {
    setNovelInfo(draft.novelInfo);
    setUrlOrId(draft.novelInfo.bookId);
    setStartChapter(draft.startChapter);
    setEndChapter(draft.endChapter);
    setCandidates(draft.candidates);
    setDraftBanner(null);
  }

  function dismissDraft() {
    clearDraft(projectId);
    setDraftBanner(null);
  }

  async function fetchNovel(target?: string) {
    const input = (target ?? urlOrId).trim();
    if (!input) { setError('Vui lòng nhập Link hoặc ID tiểu thuyết Wenku'); return; }
    setLoadingInfo(true);
    setError('');
    setCandidates([]);
    try {
      const data = await apiJson<WenkuNovelInfo>(
        `/api/wenku/info?urlOrId=${encodeURIComponent(input)}`,
      );
      setNovelInfo(data);
      setUrlOrId(data.bookId);
      setStartChapter(1);
      setEndChapter(Math.min(10, data.totalChapters || 10));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Không thể lấy thông tin truyện từ Wenku');
    } finally {
      setLoadingInfo(false);
    }
  }

  async function loadRankings(type: string) {
    setActiveRank(type);
    setLoadingRank(true);
    try {
      const data = await apiJson<{ rankings: RankItem[] }>(`/api/wenku/rankings?type=${type}`);
      setRankings(data.rankings ?? []);
    } catch {
      setRankings([]);
    } finally {
      setLoadingRank(false);
    }
  }

  useEffect(() => {
    if (showRankings && rankings.length === 0) void loadRankings('hot');
  }, [showRankings]);

  async function startCrawl() {
    if (!novelInfo) return;
    setCrawling(true);
    setError('');
    setCandidates([]);
    setCrawlState({ crawling: true, bookTitle: novelInfo.title, startChapter, endChapter });
    try {
      const resp = await apiJson<{ candidates: ImportCandidate[] }>('/api/wenku/preview', {
        method: 'POST',
        body: { url_or_id: novelInfo.bookId, start_chapter: startChapter, end_chapter: endChapter },
      });
      if (!resp.candidates || resp.candidates.length === 0) {
        throw new Error('Không lấy được nội dung chương nào từ Wenku');
      }
      setCandidates(resp.candidates);
      // Lưu vào localStorage ngay khi cào xong
      saveDraft(projectId, {
        novelInfo,
        candidates: resp.candidates,
        startChapter,
        endChapter,
        savedAt: new Date().toISOString(),
      });
      setDraftBanner(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Lỗi trong quá trình cào dữ liệu');
    } finally {
      setCrawling(false);
      setCrawlState(null);
    }
  }

  async function confirmImport(mappedCandidates: ImportCandidate[]) {
    if (!projectId) return;
    const items = mappedCandidates.map((c) => ({
      ordinal: c.ordinal,
      title: c.title ?? `Chương ${c.ordinal}`,
      text: c.text,
    }));
    if (items.some((item) => item.ordinal === null || item.ordinal <= 0 || !item.text.trim())) {
      setError('Có chương chưa có số thứ tự hoặc không có nội dung.');
      return;
    }
    setConfirming(true);
    setError('');
    try {
      const payload = await apiJson<{ chapters: { id: string }[] }>(
        `/api/projects/${projectId}/chapters/import`,
        { method: 'POST', body: { kind: 'PASTE', items } },
      );
      if (payload.chapters && payload.chapters.length > 0) {
        clearDraft(projectId); // Xoá draft sau khi nhập thành công
        onImportSuccess(payload.chapters[0].id);
      } else {
        throw new Error('Không có chương nào được tạo.');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Lỗi khi nhập chương vào dự án');
    } finally {
      setConfirming(false);
    }
  }

  function setPresetRange(count: number) {
    if (!novelInfo) return;
    setStartChapter(1);
    setEndChapter(Math.min(count, novelInfo.totalChapters));
  }

  // Tính % tiến trình ước tính
  const totalToCrawl = endChapter - startChapter + 1;
  const estimatedTotalSecs = totalToCrawl * SECS_PER_CHAPTER;
  const estimatedPercent = crawling
    ? Math.min(95, Math.round((crawlElapsed / estimatedTotalSecs) * 100))
    : 0;
  const estimatedDone = Math.min(totalToCrawl - 1, Math.round(crawlElapsed / SECS_PER_CHAPTER));
  const remainingSecs = Math.max(0, Math.round(estimatedTotalSecs - crawlElapsed));
  const remainingMin = Math.ceil(remainingSecs / 60);

  return (
    <div className={styles.container}>

      {/* Banner khôi phục phiên cào cũ */}
      {draftBanner && !crawling && candidates.length === 0 ? (
        <div className={styles.restoreBanner} role="alert">
          <span className={styles.restoreIcon}>⚠️</span>
          <div className={styles.restoreText}>
            <strong>Phiên cào trước chưa nhập</strong> — {draftBanner.candidates.length} chương từ{' '}
            <em>{draftBanner.novelInfo.title}</em>
            <span className={styles.restoreTime}>
              {' '}(lưu lúc {new Date(draftBanner.savedAt).toLocaleTimeString('vi-VN')})
            </span>
          </div>
          <div className={styles.restoreActions}>
            <button type="button" onClick={() => applyDraft(draftBanner)} className={styles.restoreBtn}>
              Dùng lại
            </button>
            <button type="button" onClick={dismissDraft} className={styles.dismissBtn}>
              Xoá
            </button>
          </div>
        </div>
      ) : null}

      {/* Khối nhập link / ID */}
      <div className={styles.card}>
        <h2 className={styles.cardTitle}>🌐 Nhập từ Wenku (QQ Reading)</h2>
        <p className={styles.cardSubtitle}>
          Dán đường link truyện (ví dụ <code>https://wenku.read.qq.com/detail/1059978960</code>) hoặc Book ID để tự động lấy thông tin và cào chương.
        </p>

        <div className={styles.inputRow}>
          <input
            type="text"
            value={urlOrId}
            onChange={(e) => setUrlOrId(e.target.value)}
            placeholder="Dán URL hoặc nhập Book ID..."
            className={styles.textInput}
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); void fetchNovel(); } }}
          />
          <button
            type="button"
            onClick={() => void fetchNovel()}
            disabled={loadingInfo || crawling || !urlOrId.trim()}
            className={styles.primaryBtn}
          >
            {loadingInfo ? '⏳ Đang kiểm tra...' : '🔍 Lấy thông tin truyện'}
          </button>
          <button
            type="button"
            onClick={() => {
              setShowRankings(!showRankings);
              if (!showRankings && rankings.length === 0) void loadRankings(activeRank);
            }}
            className={styles.secondaryBtn}
          >
            {showRankings ? 'Ẩn bảng xếp hạng' : '🏆 Chọn từ BXH'}
          </button>
        </div>

        {showRankings ? (
          <div className={styles.rankingsBox}>
            <div className={styles.rankTabs}>
              {RANK_TABS.map((tab) => (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => void loadRankings(tab.id)}
                  className={activeRank === tab.id ? `${styles.rankTabBtn} ${styles.rankTabBtnActive}` : styles.rankTabBtn}
                >
                  {tab.label}
                </button>
              ))}
            </div>
            {loadingRank ? (
              <p className={styles.loadingText}>Đang tải danh sách bảng xếp hạng...</p>
            ) : (
              <div className={styles.rankGrid}>
                {rankings.map((book, idx) => (
                  <button
                    key={book.bookId}
                    type="button"
                    onClick={() => { setUrlOrId(book.bookId); void fetchNovel(book.bookId); }}
                    className={styles.rankItem}
                  >
                    <span className={styles.rankBadge}>{idx + 1}</span>
                    <span className={styles.rankTitle}>{book.title}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        ) : null}
      </div>

      {/* Card thông tin truyện */}
      {novelInfo ? (
        <div className={styles.novelCard}>
          <div className={styles.novelHeader}>
            {novelInfo.coverUrl ? (
              <img
                src={novelInfo.coverUrl}
                alt={novelInfo.title}
                className={styles.novelCover}
                onError={(e) => { (e.target as HTMLElement).style.display = 'none'; }}
              />
            ) : (
              <div className={styles.coverPlaceholder}>Bìa truyện</div>
            )}
            <div className={styles.novelMeta}>
              <h3 className={styles.novelTitle}>{novelInfo.title}</h3>
              <p className={styles.metaRow}>
                <strong>Tác giả:</strong> <span className={styles.authorBadge}>{novelInfo.author}</span>
                <span className={styles.metaDivider}>•</span>
                <strong>Thể loại:</strong> {novelInfo.category}
                <span className={styles.metaDivider}>•</span>
                <strong>Trạng thái:</strong> {novelInfo.status}
                <span className={styles.metaDivider}>•</span>
                <strong>Số chữ:</strong> {novelInfo.wordCountText || 'Đang cập nhật'}
              </p>
              <p className={styles.metaRow}>
                <strong>Tổng số chương:</strong>{' '}
                <span className={styles.chapterBadge}>{novelInfo.totalChapters} chương</span>
                {novelInfo.latestChapter ? (
                  <>
                    <span className={styles.metaDivider}>•</span>
                    <strong>Mới nhất:</strong> {novelInfo.latestChapter}
                  </>
                ) : null}
              </p>
              <div className={styles.descBox}>
                <strong>Giới thiệu:</strong>
                <p className={styles.descText}>{novelInfo.description || 'Chưa có mô tả.'}</p>
              </div>
            </div>
          </div>

          {/* Chọn phạm vi cào */}
          <div className={styles.crawlConfig}>
            <div className={styles.rangeRow}>
              <span className={styles.rangeLabel}>Phạm vi cào:</span>
              <label className={styles.numLabel}>
                Từ chương:
                <input
                  type="number" min={1} max={novelInfo.totalChapters} value={startChapter}
                  onChange={(e) => setStartChapter(Math.max(1, parseInt(e.target.value) || 1))}
                  className={styles.numberInput} disabled={crawling}
                />
              </label>
              <label className={styles.numLabel}>
                Đến chương:
                <input
                  type="number" min={startChapter} max={novelInfo.totalChapters} value={endChapter}
                  onChange={(e) =>
                    setEndChapter(Math.min(novelInfo.totalChapters, Math.max(startChapter, parseInt(e.target.value) || startChapter)))
                  }
                  className={styles.numberInput} disabled={crawling}
                />
              </label>
              <div className={styles.presetButtons}>
                {[10, 30, 50].map((n) => (
                  <button key={n} type="button" onClick={() => setPresetRange(n)} className={styles.presetBtn} disabled={crawling}>
                    {n} chương
                  </button>
                ))}
                <button type="button" onClick={() => setPresetRange(novelInfo.totalChapters)} className={styles.presetBtn} disabled={crawling}>
                  Tất cả ({novelInfo.totalChapters})
                </button>
              </div>
            </div>

            {/* Animated Progress Bar (hiện khi đang cào) */}
            {crawling ? (
              <div className={styles.progressWrap}>
                <div className={styles.progressHeader}>
                  <span className={styles.progressLabel}>
                    <span className={styles.spinIcon}>⚙️</span>
                    Đang cào <strong>{Math.min(estimatedDone + 1, totalToCrawl)}</strong>/{totalToCrawl} chương
                    {remainingSecs > 10
                      ? ` — còn ~${remainingMin > 1 ? `${remainingMin} phút` : `${remainingSecs} giây`}`
                      : ' — sắp xong...'}
                  </span>
                  <span className={styles.progressPercent}>{estimatedPercent}%</span>
                </div>
                <div className={styles.progressTrack}>
                  <div className={styles.progressFill} style={{ width: `${estimatedPercent}%` }} />
                  <div className={styles.shimmerOverlay} />
                </div>
                <p className={styles.progressNote}>
                  <span className={styles.pulseDot}>●</span>
                  Đừng đóng tab hoặc reload — dữ liệu được lưu tự động khi cào xong.
                </p>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => void startCrawl()}
                disabled={loadingInfo}
                className={styles.crawlBtn}
              >
                {`🚀 Bắt đầu cào ${Math.max(0, endChapter - startChapter + 1)} chương & Xem trước`}
              </button>
            )}
          </div>
        </div>
      ) : null}

      {error ? <div className={styles.errorBox} role="alert">⚠️ {error}</div> : null}

      {/* Preview kết quả + nút xác nhận */}
      {candidates.length > 0 ? (
        <div className={styles.previewContainer}>
          <div className={styles.previewBanner}>
            <span>
              ✅ Đã cào thành công <strong>{candidates.length}</strong> chương.
              Kiểm tra nội dung bên dưới rồi bấm{' '}
              <strong>Xác nhận nhập vào dự án</strong> để sang khâu dịch.
            </span>
            <button
              type="button"
              onClick={() => void confirmImport(candidates)}
              disabled={confirming}
              className={styles.confirmBtn}
            >
              {confirming ? 'Đang nhập...' : '📥 Xác nhận nhập vào dự án'}
            </button>
          </div>
          <ImportPreview candidates={candidates} onConfirm={(mapped) => void confirmImport(mapped)} />
        </div>
      ) : null}
    </div>
  );
}
