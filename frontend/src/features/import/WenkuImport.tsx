import { useState, useEffect, useRef } from 'react';
import { apiJson } from '../../shared/api';
import ImportPreview, { type ImportCandidate } from './ImportPreview';
import { useWenkuCrawl } from './WenkuCrawlContext';

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

// Inject CSS animation 1 lần duy nhất
let cssInjected = false;
function injectCrawlCss() {
  if (cssInjected || typeof document === 'undefined') return;
  cssInjected = true;
  const style = document.createElement('style');
  style.textContent = `
    @keyframes wenku-shimmer {
      0%   { background-position: -400px 0; }
      100% { background-position:  400px 0; }
    }
    @keyframes wenku-pulse {
      0%, 100% { opacity: 1;   transform: scale(1);   }
      50%       { opacity: 0.4; transform: scale(0.75); }
    }
    @keyframes wenku-spin {
      from { transform: rotate(0deg);   }
      to   { transform: rotate(360deg); }
    }
    .wenku-shimmer-overlay {
      position: absolute; inset: 0;
      background: linear-gradient(
        90deg,
        transparent 0%,
        rgba(255,255,255,0.55) 50%,
        transparent 100%
      );
      background-size: 400px 100%;
      animation: wenku-shimmer 1.5s linear infinite;
    }
    .wenku-pulse-dot  { animation: wenku-pulse 1.2s ease-in-out infinite; }
    .wenku-spin-icon  { display: inline-block; animation: wenku-spin 1.5s linear infinite; }
  `;
  document.head.appendChild(style);
}

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

  // Inject CSS một lần
  useEffect(() => { injectCrawlCss(); }, []);

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
    <div style={s.container}>

      {/* Banner khôi phục phiên cào cũ */}
      {draftBanner && !crawling && candidates.length === 0 ? (
        <div style={s.restoreBanner} role="alert">
          <span style={{ fontSize: 20, flexShrink: 0 }}>⚠️</span>
          <div style={s.restoreText}>
            <strong>Phiên cào trước chưa nhập</strong> — {draftBanner.candidates.length} chương từ{' '}
            <em>{draftBanner.novelInfo.title}</em>
            <span style={s.restoreTime}>
              {' '}(lưu lúc {new Date(draftBanner.savedAt).toLocaleTimeString('vi-VN')})
            </span>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button type="button" onClick={() => applyDraft(draftBanner)} style={s.restoreBtn}>
              Dùng lại
            </button>
            <button type="button" onClick={dismissDraft} style={s.dismissBtn}>
              Xoá
            </button>
          </div>
        </div>
      ) : null}

      {/* Khối nhập link / ID */}
      <div style={s.card}>
        <h2 style={s.cardTitle}>🌐 Nhập từ Wenku (QQ Reading)</h2>
        <p style={s.cardSubtitle}>
          Dán đường link truyện (ví dụ <code>https://wenku.read.qq.com/detail/1059978960</code>) hoặc Book ID để tự động lấy thông tin và cào chương.
        </p>

        <div style={s.inputRow}>
          <input
            type="text"
            value={urlOrId}
            onChange={(e) => setUrlOrId(e.target.value)}
            placeholder="Dán URL hoặc nhập Book ID..."
            style={s.textInput}
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); void fetchNovel(); } }}
          />
          <button
            type="button"
            onClick={() => void fetchNovel()}
            disabled={loadingInfo || crawling || !urlOrId.trim()}
            style={s.primaryBtn}
          >
            {loadingInfo ? '⏳ Đang kiểm tra...' : '🔍 Lấy thông tin truyện'}
          </button>
          <button
            type="button"
            onClick={() => {
              setShowRankings(!showRankings);
              if (!showRankings && rankings.length === 0) void loadRankings(activeRank);
            }}
            style={s.secondaryBtn}
          >
            {showRankings ? 'Ẩn bảng xếp hạng' : '🏆 Chọn từ BXH'}
          </button>
        </div>

        {showRankings ? (
          <div style={s.rankingsBox}>
            <div style={s.rankTabs}>
              {RANK_TABS.map((tab) => (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => void loadRankings(tab.id)}
                  style={{ ...s.rankTabBtn, ...(activeRank === tab.id ? s.rankTabBtnActive : {}) }}
                >
                  {tab.label}
                </button>
              ))}
            </div>
            {loadingRank ? (
              <p style={s.loadingText}>Đang tải danh sách bảng xếp hạng...</p>
            ) : (
              <div style={s.rankGrid}>
                {rankings.map((book, idx) => (
                  <div
                    key={book.bookId}
                    onClick={() => { setUrlOrId(book.bookId); void fetchNovel(book.bookId); }}
                    style={s.rankItem}
                  >
                    <span style={s.rankBadge}>{idx + 1}</span>
                    <span style={s.rankTitle}>{book.title}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        ) : null}
      </div>

      {/* Card thông tin truyện */}
      {novelInfo ? (
        <div style={s.novelCard}>
          <div style={s.novelHeader}>
            {novelInfo.coverUrl ? (
              <img
                src={novelInfo.coverUrl}
                alt={novelInfo.title}
                style={s.novelCover}
                onError={(e) => { (e.target as HTMLElement).style.display = 'none'; }}
              />
            ) : (
              <div style={s.coverPlaceholder}>Bìa truyện</div>
            )}
            <div style={s.novelMeta}>
              <h3 style={s.novelTitle}>{novelInfo.title}</h3>
              <p style={s.metaRow}>
                <strong>Tác giả:</strong> <span style={s.authorBadge}>{novelInfo.author}</span>
                <span style={s.metaDivider}>•</span>
                <strong>Thể loại:</strong> {novelInfo.category}
                <span style={s.metaDivider}>•</span>
                <strong>Trạng thái:</strong> {novelInfo.status}
                <span style={s.metaDivider}>•</span>
                <strong>Số chữ:</strong> {novelInfo.wordCountText || 'Đang cập nhật'}
              </p>
              <p style={s.metaRow}>
                <strong>Tổng số chương:</strong>{' '}
                <span style={s.chapterBadge}>{novelInfo.totalChapters} chương</span>
                {novelInfo.latestChapter ? (
                  <>
                    <span style={s.metaDivider}>•</span>
                    <strong>Mới nhất:</strong> {novelInfo.latestChapter}
                  </>
                ) : null}
              </p>
              <div style={s.descBox}>
                <strong>Giới thiệu:</strong>
                <p style={s.descText}>{novelInfo.description || 'Chưa có mô tả.'}</p>
              </div>
            </div>
          </div>

          {/* Chọn phạm vi cào */}
          <div style={s.crawlConfig}>
            <div style={s.rangeRow}>
              <span style={s.rangeLabel}>Phạm vi cào:</span>
              <label style={s.numLabel}>
                Từ chương:
                <input
                  type="number" min={1} max={novelInfo.totalChapters} value={startChapter}
                  onChange={(e) => setStartChapter(Math.max(1, parseInt(e.target.value) || 1))}
                  style={s.numberInput} disabled={crawling}
                />
              </label>
              <label style={s.numLabel}>
                Đến chương:
                <input
                  type="number" min={startChapter} max={novelInfo.totalChapters} value={endChapter}
                  onChange={(e) =>
                    setEndChapter(Math.min(novelInfo.totalChapters, Math.max(startChapter, parseInt(e.target.value) || startChapter)))
                  }
                  style={s.numberInput} disabled={crawling}
                />
              </label>
              <div style={s.presetButtons}>
                {[10, 30, 50].map((n) => (
                  <button key={n} type="button" onClick={() => setPresetRange(n)} style={s.presetBtn} disabled={crawling}>
                    {n} chương
                  </button>
                ))}
                <button type="button" onClick={() => setPresetRange(novelInfo.totalChapters)} style={s.presetBtn} disabled={crawling}>
                  Tất cả ({novelInfo.totalChapters})
                </button>
              </div>
            </div>

            {/* Animated Progress Bar (hiện khi đang cào) */}
            {crawling ? (
              <div style={s.progressWrap}>
                <div style={s.progressHeader}>
                  <span style={s.progressLabel}>
                    <span className="wenku-spin-icon" style={{ marginRight: 6 }}>⚙️</span>
                    Đang cào <strong>{Math.min(estimatedDone + 1, totalToCrawl)}</strong>/{totalToCrawl} chương
                    {remainingSecs > 10
                      ? ` — còn ~${remainingMin > 1 ? `${remainingMin} phút` : `${remainingSecs} giây`}`
                      : ' — sắp xong...'}
                  </span>
                  <span style={s.progressPercent}>{estimatedPercent}%</span>
                </div>
                <div style={s.progressTrack}>
                  <div style={{ ...s.progressFill, width: `${estimatedPercent}%` }} />
                  <div className="wenku-shimmer-overlay" />
                </div>
                <p style={s.progressNote}>
                  <span className="wenku-pulse-dot" style={{ color: '#16a34a', marginRight: 4 }}>●</span>
                  Đừng đóng tab hoặc reload — dữ liệu được lưu tự động khi cào xong.
                </p>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => void startCrawl()}
                disabled={loadingInfo}
                style={s.crawlBtn}
              >
                {`🚀 Bắt đầu cào ${Math.max(0, endChapter - startChapter + 1)} chương & Xem trước`}
              </button>
            )}
          </div>
        </div>
      ) : null}

      {error ? <div style={s.errorBox} role="alert">⚠️ {error}</div> : null}

      {/* Preview kết quả + nút xác nhận */}
      {candidates.length > 0 ? (
        <div style={s.previewContainer}>
          <div style={s.previewBanner}>
            <span>
              ✅ Đã cào thành công <strong>{candidates.length}</strong> chương.
              Kiểm tra nội dung bên dưới rồi bấm{' '}
              <strong>Xác nhận nhập vào dự án</strong> để sang khâu dịch.
            </span>
            <button
              type="button"
              onClick={() => void confirmImport(candidates)}
              disabled={confirming}
              style={s.confirmBtn}
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

const s: Record<string, React.CSSProperties> = {
  container: { display: 'flex', flexDirection: 'column', gap: 20 },

  // Restore banner
  restoreBanner: {
    display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
    padding: '12px 16px', background: '#fffbeb',
    border: '1px solid #fcd34d', borderRadius: 8,
  },
  restoreText: { flex: 1, fontSize: 14, color: '#78350f', minWidth: 200 },
  restoreTime: { color: '#92400e', fontSize: 12 },
  restoreBtn: {
    padding: '6px 14px', background: '#d97706', color: '#fff',
    border: 0, borderRadius: 6, fontWeight: 700, fontSize: 13, cursor: 'pointer',
  },
  dismissBtn: {
    padding: '6px 14px', background: '#fff', color: '#78350f',
    border: '1px solid #fcd34d', borderRadius: 6, fontSize: 13, cursor: 'pointer',
  },

  // Card nhập URL
  card: {
    background: '#fff', border: '1px solid #e2e8f0', borderRadius: 10,
    padding: 20, boxShadow: '0 2px 6px rgba(0,0,0,0.04)',
  },
  cardTitle: { margin: '0 0 8px', fontSize: 20, color: '#0f172a', fontWeight: 700 },
  cardSubtitle: { margin: '0 0 16px', fontSize: 14, color: '#64748b', lineHeight: 1.5 },
  inputRow: { display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' },
  textInput: {
    flex: 1, minWidth: 280, padding: '10px 14px',
    borderRadius: 6, border: '1px solid #cbd5e1', fontSize: 14, outline: 'none',
  },
  primaryBtn: {
    padding: '10px 18px', background: '#0284c7', color: '#fff',
    border: 0, borderRadius: 6, fontWeight: 600, fontSize: 14, cursor: 'pointer',
  },
  secondaryBtn: {
    padding: '10px 14px', background: '#f1f5f9', color: '#334155',
    border: '1px solid #cbd5e1', borderRadius: 6, fontSize: 14, fontWeight: 600, cursor: 'pointer',
  },

  // Rankings
  rankingsBox: { marginTop: 16, padding: 14, background: '#f8fafc', borderRadius: 8, border: '1px solid #e2e8f0' },
  rankTabs: { display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' },
  rankTabBtn: {
    padding: '6px 12px', background: '#fff', border: '1px solid #cbd5e1',
    borderRadius: 4, fontSize: 13, cursor: 'pointer', color: '#475569',
  },
  rankTabBtnActive: { background: '#0284c7', color: '#fff', borderColor: '#0284c7', fontWeight: 600 },
  loadingText: { color: '#64748b', fontSize: 13, fontStyle: 'italic' },
  rankGrid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 8, maxHeight: 220, overflowY: 'auto' },
  rankItem: {
    display: 'flex', alignItems: 'center', gap: 8, padding: '6px 10px',
    background: '#fff', border: '1px solid #e2e8f0', borderRadius: 4, cursor: 'pointer', fontSize: 13,
  },
  rankBadge: { background: '#e0f2fe', color: '#0369a1', fontWeight: 700, borderRadius: 3, padding: '2px 6px', fontSize: 11 },
  rankTitle: { whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', color: '#1e293b' },

  // Novel card
  novelCard: {
    background: '#fff', border: '1px solid #e2e8f0', borderRadius: 10, padding: 20,
    boxShadow: '0 2px 6px rgba(0,0,0,0.04)', display: 'flex', flexDirection: 'column', gap: 16,
  },
  novelHeader: { display: 'flex', gap: 20, flexWrap: 'wrap' },
  novelCover: { width: 130, height: 175, borderRadius: 6, objectFit: 'cover', boxShadow: '0 4px 8px rgba(0,0,0,0.1)' },
  coverPlaceholder: {
    width: 130, height: 175, background: '#e2e8f0', borderRadius: 6,
    display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#94a3b8', fontSize: 13,
  },
  novelMeta: { flex: 1, minWidth: 280, display: 'flex', flexDirection: 'column', gap: 6 },
  novelTitle: { margin: 0, fontSize: 22, color: '#0f172a', fontWeight: 700 },
  metaRow: { margin: 0, fontSize: 14, color: '#334155', display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' },
  metaDivider: { color: '#cbd5e1', margin: '0 4px' },
  authorBadge: { color: '#0284c7', fontWeight: 600 },
  chapterBadge: { background: '#dcfce7', color: '#15803d', padding: '2px 8px', borderRadius: 4, fontWeight: 700, fontSize: 13 },
  descBox: { marginTop: 6, fontSize: 13, color: '#475569', background: '#f8fafc', padding: 10, borderRadius: 6, border: '1px solid #f1f5f9' },
  descText: { margin: '4px 0 0', lineHeight: 1.5, maxHeight: 80, overflowY: 'auto' },

  // Crawl config
  crawlConfig: { paddingTop: 14, borderTop: '1px solid #f1f5f9', display: 'flex', flexDirection: 'column', gap: 12 },
  rangeRow: { display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' },
  rangeLabel: { fontWeight: 600, fontSize: 14, color: '#334155' },
  numLabel: { display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: '#475569' },
  numberInput: { width: 80, padding: '6px 8px', border: '1px solid #cbd5e1', borderRadius: 4, fontSize: 14 },
  presetButtons: { display: 'flex', gap: 6, flexWrap: 'wrap' },
  presetBtn: {
    padding: '5px 10px', background: '#f1f5f9', border: '1px solid #cbd5e1',
    borderRadius: 4, fontSize: 12, color: '#334155', cursor: 'pointer',
  },

  // Animated progress bar
  progressWrap: {
    padding: '14px 16px', background: '#f0fdf4', border: '1px solid #bbf7d0',
    borderRadius: 8, display: 'flex', flexDirection: 'column', gap: 8,
  },
  progressHeader: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 14, color: '#166534' },
  progressLabel: { display: 'flex', alignItems: 'center', gap: 4 },
  progressPercent: { fontWeight: 700, fontSize: 18, color: '#15803d', minWidth: 42, textAlign: 'right' },
  progressTrack: {
    position: 'relative', height: 12, background: '#dcfce7', borderRadius: 99, overflow: 'hidden',
  },
  progressFill: {
    position: 'absolute', left: 0, top: 0, height: '100%',
    background: 'linear-gradient(90deg, #16a34a 0%, #4ade80 100%)',
    borderRadius: 99, transition: 'width 1s linear',
    minWidth: 8,
  },
  progressNote: {
    margin: 0, fontSize: 12, color: '#166534',
    display: 'flex', alignItems: 'center', gap: 4,
  },

  crawlBtn: {
    padding: '12px 20px', background: '#16a34a', color: '#fff',
    border: 0, borderRadius: 6, fontSize: 15, fontWeight: 700, cursor: 'pointer',
    width: '100%', textAlign: 'center',
  },

  // Error & preview
  errorBox: { padding: 12, background: '#fef2f2', color: '#b91c1c', border: '1px solid #fecaca', borderRadius: 6, fontSize: 14 },
  previewContainer: { display: 'flex', flexDirection: 'column', gap: 12 },
  previewBanner: {
    padding: 14, background: '#ecfdf5', border: '1px solid #a7f3d0', borderRadius: 8,
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    gap: 16, flexWrap: 'wrap', fontSize: 14, color: '#065f46',
  },
  confirmBtn: {
    padding: '10px 18px', background: '#059669', color: '#fff',
    border: 0, borderRadius: 6, fontWeight: 700, fontSize: 14, cursor: 'pointer', whiteSpace: 'nowrap',
  },
};
