import { Link, useNavigate, useParams } from 'react-router-dom';
import { useEffect, useState } from 'react';
import { QualityPlanPanel } from '../../features/providers/QualityPlanPanel';
import { apiJson } from '../../shared/api';
import styles from './TranslationScreen.module.css';

type TranslationIssue = {
  id: string;
  category: string;
  severity: string;
  status: string;
  evidence?: string | null;
  suggestion?: string | null;
  sourceSegmentId?: string | null;
};

type TranslationPayload = {
  run: {
    id: string;
    status: string;
    sha256: string;
  };
  segments: {
    sourceSegmentId: string;
    sourceText: string;
    targetText: string;
  }[];
  issues: TranslationIssue[];
};

export function TranslationScreen() {
  const { chapterId } = useParams();
  const navigate = useNavigate();
  const [data, setData] = useState<TranslationPayload | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [editingId, setEditingId] = useState<string | null>(null);
  const [savingId, setSavingId] = useState<string | null>(null);
  const [filter, setFilter] = useState<'ALL' | 'ISSUES_ONLY' | 'BLOCKERS_ONLY'>('ALL');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [geminiApiKey, setGeminiApiKey] = useState('');
  const [geminiProfileId, setGeminiProfileId] = useState('');
  const [qwenProfileId, setQwenProfileId] = useState('');
  const [showKey, setShowKey] = useState(false);
  const [cloudConsentId, setCloudConsentId] = useState('');
  const [budgetAuthorizationId, setBudgetAuthorizationId] = useState('');

  useEffect(() => {
    if (!chapterId) {
      return;
    }
    let cancelled = false;
    apiJson<TranslationPayload>(`/api/chapters/${chapterId}/translation`)
      .then((payload) => {
        if (!cancelled) {
          setData(payload);
          setDrafts(Object.fromEntries(payload.segments.map((s) => [s.sourceSegmentId, s.targetText])));
          setMessage('Đã tải bản dịch hiện tại');
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [chapterId]);

  async function translateGemini() {
    if (!chapterId) {
      return;
    }
    const profileId = geminiProfileId.trim();
    if (!profileId || !cloudConsentId.trim() || !budgetAuthorizationId.trim()) {
      setError('GEMINI_PROFILE_CONSENT_AND_BUDGET_REQUIRED');
      return;
    }
    setBusy(true);
    setError('');
    setMessage('Đang dịch chương truyện bằng Google Gemini... Vui lòng chờ vài giây.');
    try {
      const payload = await apiJson<TranslationPayload>(`/api/chapters/${chapterId}/translation/gemini`, {
        method: 'POST',
        body: {
          profileId,
          cloudConsentId: cloudConsentId.trim(),
          budgetAuthorizationId: budgetAuthorizationId.trim(),
        },
      });
      setData(payload);
      setDrafts(Object.fromEntries(payload.segments.map((s) => [s.sourceSegmentId, s.targetText])));
      setMessage('Đã dịch xong toàn bộ chương bằng Google Gemini! Văn phong tiểu thuyết tự nhiên, mượt mà.');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'GEMINI_TRANSLATION_FAILED');
    } finally {
      setBusy(false);
    }
  }

  async function provisionGeminiCredential() {
    const profileId = geminiProfileId.trim();
    const secret = geminiApiKey.trim();
    if (!profileId || !secret) {
      setError('GEMINI_PROFILE_AND_CREDENTIAL_REQUIRED');
      return;
    }
    setBusy(true);
    setError('');
    try {
      await apiJson(`/api/cloud-profiles/${profileId}/credential`, {
        method: 'PUT',
        body: { secret },
      });
      setMessage('Đã lưu credential vào keyring cục bộ và xóa key khỏi biểu mẫu.');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'CREDENTIAL_PROVISIONING_FAILED');
    } finally {
      setGeminiApiKey('');
      setBusy(false);
    }
  }

  async function translateFake() {
    if (!chapterId) {
      return;
    }
    setBusy(true);
    setError('');
    setMessage('Đang dịch nhanh bằng bộ dịch Convert / Fake nội bộ...');
    try {
      const payload = await apiJson<TranslationPayload>(`/api/chapters/${chapterId}/translation/fake`, {
        method: 'POST',
        body: {},
      });
      setData(payload);
      setDrafts(Object.fromEntries(payload.segments.map((s) => [s.sourceSegmentId, s.targetText])));
      setMessage('Đã dịch hoàn tất bằng bộ dịch Convert / Fake nội bộ (Hán-Việt)!');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'TRANSLATION_FAILED');
    } finally {
      setBusy(false);
    }
  }

  async function translateQwen() {
    if (!chapterId) {
      return;
    }
    if (!qwenProfileId.trim() || !cloudConsentId.trim() || !budgetAuthorizationId.trim()) {
      setError('CLOUD_CONSENT_AND_BUDGET_REQUIRED');
      return;
    }
    setBusy(true);
    setError('');
    setMessage('Đang gửi bản dịch qua Qwen AI...');
    try {
      const payload = await apiJson<TranslationPayload>(`/api/chapters/${chapterId}/translation/qwen`, {
        method: 'POST',
        body: {
          profileId: qwenProfileId.trim(),
          cloudConsentId: cloudConsentId.trim(),
          budgetAuthorizationId: budgetAuthorizationId.trim(),
        },
      });
      setData(payload);
      setDrafts(Object.fromEntries(payload.segments.map((s) => [s.sourceSegmentId, s.targetText])));
      setMessage('Dịch qua Qwen thành công! Vui lòng duyệt bản dịch.');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'QWEN_TRANSLATION_FAILED');
    } finally {
      setBusy(false);
    }
  }

  async function saveSegment(sourceSegmentId: string) {
    if (!chapterId || !data) {
      return;
    }
    setSavingId(sourceSegmentId);
    setError('');
    try {
      const updated = await apiJson<TranslationPayload>(
        `/api/chapters/${chapterId}/translation/segments/${sourceSegmentId}`,
        {
          method: 'PATCH',
          body: {
            runId: data.run.id,
            targetText: drafts[sourceSegmentId] ?? '',
            expectedRunHash: data.run.sha256,
          },
        }
      );
      setData(updated);
      setDrafts(Object.fromEntries(updated.segments.map((s) => [s.sourceSegmentId, s.targetText])));
      setEditingId(null);
      setMessage('Đã cập nhật câu dịch thành công!');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'SAVE_SEGMENT_FAILED');
    } finally {
      setSavingId(null);
    }
  }

  async function approve(force = false) {
    if (!chapterId || !data) {
      return;
    }
    setBusy(true);
    setError('');
    try {
      await apiJson(`/api/chapters/${chapterId}/translation/approve`, {
        method: 'POST',
        body: { runId: data.run.id, expectedRunHash: data.run.sha256, force },
      });
      navigate(`/chapters/${chapterId}/voice`);
    } catch (reason) {
      const msg = reason instanceof Error ? reason.message : 'TRANSLATION_APPROVAL_FAILED';
      if (msg === 'TRANSLATION_QA_BLOCKERS_OPEN') {
        setError('Bản dịch có lỗi QA nghiêm trọng chưa được giải quyết. Bạn có thể sửa câu dịch tương ứng hoặc bấm "Bỏ qua cảnh báo & Duyệt tiếp" bên dưới.');
      } else {
        setError(msg);
      }
    } finally {
      setBusy(false);
    }
  }

  const blockers = (data?.issues ?? []).filter(
    (i) => i.status === 'OPEN' && (i.severity === 'MAJOR' || i.severity === 'CRITICAL')
  );
  const otherIssues = (data?.issues ?? []).filter(
    (i) => !(i.status === 'OPEN' && (i.severity === 'MAJOR' || i.severity === 'CRITICAL'))
  );

  const displayedSegments = (data?.segments ?? []).filter((seg) => {
    if (filter === 'ALL') return true;
    const segIssues = (data?.issues ?? []).filter((i) => i.sourceSegmentId === seg.sourceSegmentId);
    if (filter === 'BLOCKERS_ONLY') {
      return segIssues.some((i) => i.status === 'OPEN' && (i.severity === 'MAJOR' || i.severity === 'CRITICAL'));
    }
    return segIssues.length > 0;
  });

  return (
    <section className={styles.workspace} aria-label="Dịch và duyệt">
      <div className={styles.navigator}>
        {data ? (
          <>
            <div className={styles.navGroup}>
              <p className={styles.navTitle}>Bản dịch:</p>
              <p className={styles.navEntry}>Revision {data.run.sha256.slice(0, 12)}</p>
              <p className={styles.navEntry}>Trạng thái: {data.run.status}</p>
              <p className={styles.navMeta}>
                Tổng {data.segments.length} đoạn · {blockers.length} lỗi QA nghiêm trọng · {otherIssues.length} cảnh báo nhỏ
              </p>
            </div>
            <div className={styles.navGroup}>
              <p className={styles.navTitle}>Lọc hiển thị:</p>
              <select
                className={styles.filterSelect}
                value={filter}
                onChange={(e) => setFilter(e.target.value as typeof filter)}
              >
                <option value="ALL">Tất cả ({data.segments.length})</option>
                <option value="ISSUES_ONLY">Có vấn đề QA ({(data.issues ?? []).length})</option>
                <option value="BLOCKERS_ONLY">Chỉ lỗi nghiêm trọng ({blockers.length})</option>
              </select>
            </div>
          </>
        ) : null}
      </div>

      <div className={styles.editor}>
        <div className={styles.editorBody}>
          <div className={styles.headerRow}>
            <h1 className={styles.title}>Dịch & Hiệu đính</h1>
            <Link to={`/chapters/${chapterId}/voice`} className={styles.secondaryButton}>
              Chuyển sang Giọng đọc (Voice) →
            </Link>
          </div>

          {/* 🌟 Google AI Studio Gemini Translation - Card Chính */}
          <section
            className={styles.geminiCard}
            aria-label="Google AI Studio Gemini translation"
          >
            <div className={styles.geminiHead}>
              <div className={styles.geminiIntro}>
                <span className={styles.geminiMark}>✨</span>
                <div>
                  <h2 className={styles.geminiTitle}>
                    Dịch bằng Google AI Studio (Gemini Model)
                  </h2>
                  <p className={styles.geminiHint}>
                    Loại bỏ bản dịch test fake · Văn phong tiểu thuyết mượt mà, hỗ trợ thuật ngữ và bộ nhớ truyện
                  </p>
                </div>
              </div>
              <a
                href="https://aistudio.google.com/app/apikey"
                target="_blank"
                rel="noreferrer"
                className={styles.keyLink}
              >
                🔑 Lấy API Key miễn phí tại Google AI Studio ↗
              </a>
            </div>

            <div className={styles.grid2}>
              <div className={styles.panelBody}>
                <label className={styles.fieldLabel}>
                  Google AI Studio API Key
                </label>
                <div className={styles.keyRow}>
                  <input
                    type={showKey ? 'text' : 'password'}
                    value={geminiApiKey}
                    onChange={(e) => setGeminiApiKey(e.target.value)}
                    placeholder="Dán AIzaSy... từ aistudio.google.com"
                    className={[
                      styles.input,
                      styles.keyInput,
                      geminiApiKey ? styles.keyInputFilled : styles.keyInputEmpty,
                    ].join(' ')}
                    autoComplete="off"
                  />
                  <button
                    type="button"
                    onClick={() => setShowKey(!showKey)}
                    className={styles.secondaryButton}
                    title={showKey ? 'Ẩn key' : 'Hiện key'}
                  >
                    {showKey ? '🙈' : '👁️'}
                  </button>
                </div>
                <div className={styles.fieldNote}>
                  Credential chỉ tồn tại trong biểu mẫu đến khi gửi vào keyring cục bộ; không lưu trong trình duyệt hoặc dùng từ .env.
                </div>
              </div>

              <div className={styles.panelBody}>
                <label className={styles.fieldLabel}>
                  Gemini profile ID
                </label>
                <input aria-label="Gemini profile ID" value={geminiProfileId} onChange={(event) => setGeminiProfileId(event.target.value)} placeholder="Profile đã tạo trong AI Providers" className={styles.input} />
                <button type="button" onClick={() => void provisionGeminiCredential()} disabled={busy || !geminiProfileId.trim() || !geminiApiKey.trim()} className={styles.secondaryButton}>
                  Lưu API key vào profile
                </button>
              </div>

              <p className={styles.quote}>
                Model được cấu hình và kiểm soát bởi provider profile cục bộ.
              </p>
            </div>

            <div className={styles.actions}>
              <button
                type="button"
                onClick={() => void translateGemini()}
                disabled={busy || !geminiProfileId.trim() || !cloudConsentId.trim() || !budgetAuthorizationId.trim()}
                className={[styles.primaryButton, styles.geminiButton].join(' ')}
              >
                {busy ? '⏳ Đang dịch qua Gemini...' : '✨ Dịch toàn bộ chương bằng Gemini AI'}
              </button>
              {!geminiProfileId.trim() ? (
                <span className={styles.fieldWarning}>
                  ← Nhập profile Gemini và consent/budget để kích hoạt nút dịch
                </span>
              ) : null}
            </div>
          </section>

          {/* U08: per-stage quality/quote panel, only meaningful for a cloud profile. */}
          {geminiProfileId.trim() ? (
            <QualityPlanPanel
              chapterId={chapterId ?? ''}
              profileId={geminiProfileId.trim()}
              cloudConsentId={cloudConsentId.trim() || null}
              modelKey={geminiProfileId.trim()}
            />
          ) : null}

          {/* ⚙️ Tùy chọn dịch khác (Collapsible) */}
          <details open className={styles.guardBox}>
            <summary className={styles.summary}>
              ⚙️ Tùy chọn dịch khác (Qwen Cloud & Bộ Convert nội bộ)
            </summary>
            <p className={styles.quote}>
              Qwen cần consent cloud và budget authorization đã tạo trước.
            </p>
            <div className={styles.grid2}>
              <section className={styles.panelBody} aria-label="Fake test translation">
                <div className={styles.actions}>
                  <span>⚡</span>
                  <strong>Dịch nhanh nội bộ (Convert Hán-Việt)</strong>
                </div>
                <p className={styles.quote}>Dịch test convert offline, không dùng AI.</p>
                <button type="button" onClick={() => void translateFake()} disabled={busy} className={styles.secondaryButton}>
                  Dịch convert nội bộ
                </button>
              </section>

              <section className={styles.panelBody} aria-label="Qwen translation">
                <div className={styles.actions}>
                  <span>🤖</span>
                  <strong>Dịch qua Qwen AI Cloud</strong>
                </div>
                <label className={styles.label}>
                  Qwen profile ID
                  <input
                    aria-label="Qwen profile ID"
                    value={qwenProfileId}
                    onChange={(event) => setQwenProfileId(event.target.value)}
                    className={styles.input}
                    autoComplete="off"
                    placeholder="VD: profile-qwen-1"
                  />
                </label>
                <label className={styles.label}>
                  Cloud consent ID
                  <input
                    value={cloudConsentId}
                    onChange={(event) => setCloudConsentId(event.target.value)}
                    className={styles.input}
                    autoComplete="off"
                    placeholder="VD: consent-123"
                  />
                </label>
                <label className={styles.label}>
                  Budget authorization ID
                  <input
                    value={budgetAuthorizationId}
                    onChange={(event) => setBudgetAuthorizationId(event.target.value)}
                    className={styles.input}
                    autoComplete="off"
                    placeholder="VD: budget-456"
                  />
                </label>
                <button type="button" onClick={() => void translateQwen()} disabled={busy} className={styles.secondaryButton}>
                  Dịch bằng Qwen
                </button>
              </section>
            </div>
          </details>

          {message ? <p role="status" className={styles.success}>{message}</p> : null}
          {error ? <p role="alert" className={styles.error}>{error}</p> : null}

          {data ? (
            <div className={styles.reviewBox}>
              {displayedSegments.map((segment, index) => {
                const segIssues = (data.issues ?? []).filter((i) => i.sourceSegmentId === segment.sourceSegmentId);
                const isEditing = editingId === segment.sourceSegmentId;
                const isSaving = savingId === segment.sourceSegmentId;

                return (
                  <article key={segment.sourceSegmentId} className={styles.segment}>
                    <div className={styles.segmentHead}>
                      <span className={styles.segmentOrdinal}>
                        Đoạn #{index + 1}
                      </span>
                      <div className={styles.badgeRow}>
                        {segIssues.map((issue) => {
                          const isBlocker = issue.severity === 'MAJOR' || issue.severity === 'CRITICAL';
                          return (
                            <span
                              key={issue.id}
                              className={[styles.badge, isBlocker ? styles.badgeBlocker : ''].filter(Boolean).join(' ')}
                              title={issue.suggestion ?? ''}
                            >
                              {issue.severity}: {issue.category} ({issue.evidence})
                            </span>
                          );
                        })}
                      </div>
                    </div>

                    <div className={styles.split}>
                      <div className={[styles.pane, styles.paneSource].join(' ')}>
                        <span className={styles.originLabel}>GỐC:</span>
                        {segment.sourceText}
                      </div>

                      {isEditing ? (
                        <div className={styles.pane}>
                          <div className={styles.editRow}>
                            <textarea
                              value={drafts[segment.sourceSegmentId] ?? ''}
                              onChange={(e) =>
                                setDrafts({ ...drafts, [segment.sourceSegmentId]: e.target.value })
                              }
                              rows={3}
                              className={styles.textarea}
                            />
                            <div className={styles.actions}>
                              <button
                                type="button"
                                onClick={() => void saveSegment(segment.sourceSegmentId)}
                                disabled={isSaving}
                                className={styles.primaryButton}
                              >
                                {isSaving ? 'Đang lưu...' : 'Lưu bản sửa'}
                              </button>
                              <button
                                type="button"
                                onClick={() => {
                                  setDrafts({ ...drafts, [segment.sourceSegmentId]: segment.targetText });
                                  setEditingId(null);
                                }}
                                className={styles.secondaryButton}
                              >
                                Hủy
                              </button>
                            </div>
                          </div>
                        </div>
                      ) : (
                        <div className={styles.pane}>
                          <div className={styles.targetRow}>
                            <div className={styles.targetPane}>
                              <span className={styles.originLabel}>BẢN DỊCH:</span>
                              {segment.targetText}
                            </div>
                            <button
                              type="button"
                              onClick={() => setEditingId(segment.sourceSegmentId)}
                              className={styles.secondaryButton}
                            >
                              Sửa
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  </article>
                );
              })}

              <div className={styles.approveRow}>
                <button
                  type="button"
                  onClick={() => void approve(false)}
                  disabled={busy || blockers.length > 0}
                  className={styles.primaryButton}
                  title={blockers.length > 0 ? 'Cần giải quyết hoặc bỏ qua các lỗi QA nghiêm trọng trước khi duyệt' : ''}
                >
                  Phê duyệt chuẩn ({data.segments.length} đoạn)
                </button>

                {blockers.length > 0 ? (
                  <button
                    type="button"
                    onClick={() => void approve(true)}
                    disabled={busy}
                    className={styles.warnButton}
                  >
                    Bỏ qua cảnh báo QA & Phê duyệt tiếp
                  </button>
                ) : null}
              </div>
            </div>
          ) : null}
        </div>
      </div>

      <div className={styles.inspector}>
        {data && blockers.length > 0 ? (
          <div className={styles.blockers}>
            <p className={styles.blockersHead}>
              <span>⚠️</span>
              <span>Phát hiện {blockers.length} vấn đề QA nghiêm trọng (Blocker)</span>
            </p>
            <p className={styles.blockersText}>
              Các vấn đề này có thể do chênh lệch độ dài, thiếu số liệu/đơn vị hoặc thuật ngữ khóa. Bạn có thể sửa trực tiếp câu dịch ở dưới hoặc chọn bỏ qua để duyệt tiếp sang bước lồng tiếng.
            </p>
            <div className={styles.blockersActions}>
              <button
                type="button"
                onClick={() => void approve(true)}
                disabled={busy}
                className={styles.warnButton}
              >
                Bỏ qua cảnh báo & Phê duyệt (Chấp nhận rủi ro)
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}
