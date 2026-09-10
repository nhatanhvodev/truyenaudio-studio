import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { apiJson } from '../../shared/api';

import ContextInspector from './ContextInspector';
import RepairDiff from './RepairDiff';

type Run = { id: string; sha256: string; status: string };

type Segment = {
  id: string;
  sourceSegmentId: string;
  sourceText: string;
  targetText: string;
};

type Issue = {
  id: string;
  category: string;
  severity: string;
  status: string;
  evidence?: string | null;
  suggestion?: string | null;
  sourceSegmentId?: string | null;
};

type TranslationPayload = {
  run: Run;
  segments: Segment[];
  issues: Issue[];
  sourceRevisionId?: string | null;
};

type RepairReplacement = {
  sourceSegmentId: string;
  sourceText: string;
  currentTargetText: string;
  targetText: string;
};

/** U06 round 3: camelCase repair proposal as returned by repair-preview. */
type RepairProposal = {
  id: string;
  baseRunId: string;
  baseRunSha256: string;
  providerModel: string;
  storyMemoryRevisionHash: string;
  hash: string;
  estimatedCostVnd: number;
  replacements: RepairReplacement[];
};

type Props = {
  chapterId: string;
  /** Called after a successful approve (the shell navigates to the next stage). */
  onApproved?: () => void;
};

const BLOCKING_SEVERITIES = new Set(['CRITICAL']);

/**
 * U06 round 1: aligned bilingual editor with a QA inspector.
 *
 * Acceptance encoded here:
 * - rows are keyed by the **stable source segment id**, so filtering/re-ordering
 *   can never mix two paragraphs up;
 * - the source pane is read-only text (never an input): the source is only ever
 *   changed through the import flow, not through the target editor;
 * - a save is refused while an IME composition is active (`IME_COMPOSITION_ACTIVE`)
 *   because the text under the cursor is not the user's final text yet;
 * - `Ctrl/Cmd+S` saves the segment being edited;
 * - selecting a QA issue focuses and reveals its segment;
 * - an open CRITICAL issue blocks approve until it is resolved or explicitly
 *   forced, and a stale run revision (409) keeps the local text.
 */
export default function BilingualEditor({ chapterId, onApproved }: Props) {
  const [payload, setPayload] = useState<TranslationPayload | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [savingId, setSavingId] = useState<string | null>(null);
  const [composingId, setComposingId] = useState<string | null>(null);
  const [severityFilter, setSeverityFilter] = useState('ALL');
  const [selectedIssueId, setSelectedIssueId] = useState<string | null>(null);
  const [revealedSegmentId, setRevealedSegmentId] = useState<string | null>(null);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  // U06 round 3: repair preview state (proposal is immutable once returned).
  const [selectedSegmentIds, setSelectedSegmentIds] = useState<string[]>([]);
  const [repairProposal, setRepairProposal] = useState<RepairProposal | null>(null);
  const [decidedReplacements, setDecidedReplacements] = useState<Record<string, 'accepted' | 'rejected'>>({});
  const [applyingProposal, setApplyingProposal] = useState(false);
  const rowRefs = useRef<Record<string, HTMLElement | null>>({});

  useEffect(() => {
    let cancelled = false;
    apiJson<TranslationPayload>(`/api/chapters/${chapterId}/translation`)
      .then((data) => {
        if (cancelled) {
          return;
        }
        setPayload(data);
        setDrafts(Object.fromEntries(data.segments.map((segment) => [segment.sourceSegmentId, segment.targetText])));
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : 'TRANSLATION_LOAD_FAILED');
        }
      });
    return () => {
      cancelled = true;
    };
  }, [chapterId]);

  const adopt = useCallback((data: TranslationPayload) => {
    setPayload(data);
    setDrafts(Object.fromEntries(data.segments.map((segment) => [segment.sourceSegmentId, segment.targetText])));
  }, []);

  const saveSegment = useCallback(
    async (sourceSegmentId: string): Promise<boolean> => {
      if (!payload) {
        return false;
      }
      if (composingId === sourceSegmentId) {
        // The IME is still composing: the visible text is not final, so a save
        // here would store a half-typed word.
        setError('IME_COMPOSITION_ACTIVE');
        return false;
      }
      setSavingId(sourceSegmentId);
      setMessage('');
      setError('');
      try {
        const updated = await apiJson<TranslationPayload>(
          `/api/chapters/${chapterId}/translation/segments/${sourceSegmentId}`,
          {
            method: 'PATCH',
            body: {
              runId: payload.run.id,
              targetText: drafts[sourceSegmentId] ?? '',
              expectedRunHash: payload.run.sha256,
            },
          },
        );
        adopt(updated);
        setMessage('Đã lưu câu dịch');
        return true;
      } catch (reason: unknown) {
        // A revision conflict keeps the local text so nothing is lost.
        setError(reason instanceof Error ? reason.message : 'SAVE_SEGMENT_FAILED');
        return false;
      } finally {
        setSavingId(null);
      }
    },
    [adopt, chapterId, composingId, drafts, payload],
  );

  const issues = payload?.issues ?? [];
  const filteredIssues = useMemo(() => {
    if (severityFilter === 'ALL') {
      return issues;
    }
    return issues.filter((issue) => issue.severity === severityFilter);
  }, [issues, severityFilter]);

  const openCritical = issues.filter(
    (issue) => issue.status === 'OPEN' && BLOCKING_SEVERITIES.has(issue.severity),
  );

  function selectIssue(issue: Issue) {
    setSelectedIssueId(issue.id);
    const segmentId = issue.sourceSegmentId ?? null;
    setRevealedSegmentId(segmentId);
    if (segmentId) {
      rowRefs.current[segmentId]?.scrollIntoView?.({ block: 'center' });
    }
  }

  function revealSegment(sourceSegmentId: string) {
    setRevealedSegmentId(sourceSegmentId);
  }

  async function approve(force = false) {
    if (!payload) {
      return;
    }
    if (!force && openCritical.length > 0) {
      setError('TRANSLATION_QA_BLOCKERS_OPEN');
      return;
    }
    setBusy(true);
    setError('');
    try {
      await apiJson(`/api/chapters/${chapterId}/translation/approve`, {
        method: 'POST',
        body: { runId: payload.run.id, expectedRunHash: payload.run.sha256, force },
      });
      setMessage('Đã phê duyệt bản dịch');
      onApproved?.();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : 'TRANSLATION_APPROVAL_FAILED');
    } finally {
      setBusy(false);
    }
  }

  // U06 round 3: the preview button appears only when at least one segment row
  // is selected; selections stay stable across re-renders.
  function toggleSegment(sourceSegmentId: string) {
    setSelectedSegmentIds((current) =>
      current.includes(sourceSegmentId)
        ? current.filter((id) => id !== sourceSegmentId)
        : [...current, sourceSegmentId],
    );
  }

  async function previewRepair() {
    if (selectedSegmentIds.length === 0) {
      setError('REPAIR_SEGMENT_REQUIRED');
      return;
    }
    setBusy(true);
    setError('');
    setMessage('');
    try {
      const proposal = await apiJson<RepairProposal>(`/api/chapters/${chapterId}/review/repair-preview`, {
        method: 'POST',
        body: { selectedSegmentIds },
      });
      setRepairProposal(proposal);
      setDecidedReplacements({});
      setMessage('Đã có đề xuất sửa — xem xét từng dòng trước khi áp dụng.');
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : 'REPAIR_PREVIEW_FAILED');
    } finally {
      setBusy(false);
    }
  }

  // Accepting a line only edits the local draft; the server is called once the
  // whole proposal is applied ("Áp dụng đề xuất" below) with the proposal hash.
  function acceptReplacement(replacement: RepairReplacement) {
    setDecidedReplacements((current) => ({ ...current, [replacement.sourceSegmentId]: 'accepted' }));
    setDrafts((current) => ({ ...current, [replacement.sourceSegmentId]: replacement.targetText }));
  }

  function rejectReplacement(replacement: RepairReplacement) {
    setDecidedReplacements((current) => ({ ...current, [replacement.sourceSegmentId]: 'rejected' }));
  }

  async function applyRepair() {
    if (!repairProposal) {
      return;
    }
    setApplyingProposal(true);
    setError('');
    setMessage('');
    try {
      const result = await apiJson<{ runId: string; sha256: string; appliedCount: number }>(
        `/api/chapters/${chapterId}/review/repair-apply`,
        {
          method: 'POST',
          body: { proposalId: repairProposal.id, expectedProposalHash: repairProposal.hash },
        },
      );
      setMessage(`Đã áp dụng ${result.appliedCount} câu sửa vào run mới.`);
      setRepairProposal(null);
      setDecidedReplacements({});
    } catch (reason: unknown) {
      // 409/400 surface the raw server code so the operator sees the reason.
      setError(reason instanceof Error ? reason.message : 'REPAIR_APPLY_FAILED');
    } finally {
      setApplyingProposal(false);
    }
  }

  if (!payload && !error) {
    return <p role="status">Đang tải bản dịch…</p>;
  }

  return (
    <section aria-label="Editor song ngữ" style={styles.shell}>
      <header style={styles.header}>
        <div>
          <h2 style={styles.title}>Dịch &amp; hiệu đính</h2>
          <p style={styles.meta}>
            Run <span data-testid="run-id">{payload?.run.id}</span> · {payload?.run.status}
          </p>
        </div>
        <label style={styles.label}>
          Lọc QA
          <select
            aria-label="Lọc QA"
            value={severityFilter}
            onChange={(event) => setSeverityFilter(event.target.value)}
            style={styles.select}
          >
            <option value="ALL">ALL</option>
            <option value="CRITICAL">CRITICAL</option>
            <option value="MAJOR">MAJOR</option>
            <option value="MINOR">MINOR</option>
          </select>
        </label>
      </header>

      {message ? <p role="status" style={styles.success}>{message}</p> : null}
      {error ? <p role="alert" style={styles.error}>{error}</p> : null}

      <div style={styles.repairBar}>
        {selectedSegmentIds.length > 0 ? (
          <button
            type="button"
            data-testid="preview-repair"
            onClick={() => void previewRepair()}
            disabled={busy}
            style={styles.secondary}
          >
            Xem đề xuất sửa
          </button>
        ) : null}
        <span style={styles.repairHint}>
          {selectedSegmentIds.length === 0
            ? 'Chọn ít nhất một đoạn để xem đề xuất sửa.'
            : `Đã chọn ${selectedSegmentIds.length} đoạn.`}
        </span>
        {repairProposal ? (
          <button
            type="button"
            data-testid="apply-repair"
            onClick={() => void applyRepair()}
            disabled={applyingProposal}
            style={styles.primary}
          >
            {applyingProposal ? 'Đang áp dụng…' : 'Áp dụng đề xuất'}
          </button>
        ) : null}
      </div>

      {repairProposal ? (
        <RepairDiff
          proposal={{
            id: repairProposal.id,
            baseRunId: repairProposal.baseRunId,
            estimatedCostVnd: repairProposal.estimatedCostVnd,
            hash: repairProposal.hash,
            replacements: repairProposal.replacements,
          }}
          onAccept={(_proposalId, _expectedHash) => {
            // Per-line decision: the diff rows below are the authority.
            for (const replacement of repairProposal.replacements) {
              if (decidedReplacements[replacement.sourceSegmentId] !== 'rejected') {
                acceptReplacement(replacement);
              }
            }
          }}
          onReject={(proposalId) => {
            void proposalId;
            setRepairProposal(null);
            setDecidedReplacements({});
          }}
        />
      ) : null}

      {repairProposal
        ? repairProposal.replacements.map((replacement) => {
            const decision = decidedReplacements[replacement.sourceSegmentId];
            return (
              <section
                key={replacement.sourceSegmentId}
                aria-label={`Đề xuất sửa đoạn ${replacement.sourceSegmentId}`}
                style={styles.repairRow}
              >
                <div style={styles.repairActions}>
                  <strong>{replacement.sourceSegmentId}</strong>
                  {decision === 'accepted' ? (
                    <span style={styles.repairAccepted}>Đã nhận — draft đã đổi</span>
                  ) : decision === 'rejected' ? (
                    <span style={styles.repairRejected}>Đã bỏ qua</span>
                  ) : null}
                  <button
                    type="button"
                    onClick={() => acceptReplacement(replacement)}
                    style={styles.primary}
                  >
                    Chấp nhận
                  </button>
                  <button
                    type="button"
                    onClick={() => rejectReplacement(replacement)}
                    style={styles.secondary}
                  >
                    Bỏ qua
                  </button>
                </div>
              </section>
            );
          })
        : null}

      <div style={styles.layout}>
        <div style={styles.segments}>
          {(payload?.segments ?? []).map((segment) => {
            const segmentIssues = issues.filter((issue) => issue.sourceSegmentId === segment.sourceSegmentId);
            const revealed = revealedSegmentId === segment.sourceSegmentId;
            const proposal = segmentIssues.find((issue) => issue.suggestion)?.suggestion ?? null;
            return (
              <article
                key={segment.sourceSegmentId}
                ref={(node) => {
                  rowRefs.current[segment.sourceSegmentId] = node;
                }}
                data-testid={`row-${segment.sourceSegmentId}`}
                data-revealed={revealed ? 'true' : 'false'}
                style={{ ...styles.row, ...(revealed ? styles.rowRevealed : {}) }}
              >
                <div style={styles.sourceCell}>
                  <label style={styles.pick}>
                    <input
                      type="checkbox"
                      aria-label={`Chọn đoạn ${segment.sourceSegmentId}`}
                      checked={selectedSegmentIds.includes(segment.sourceSegmentId)}
                      onChange={() => toggleSegment(segment.sourceSegmentId)}
                    />
                  </label>
                  <span style={styles.cellLabel}>GỐC (chỉ đọc)</span>
                  <p
                    aria-readonly="true"
                    data-testid={`source-${segment.sourceSegmentId}`}
                    style={styles.sourceText}
                  >
                    {segment.sourceText}
                  </p>
                </div>
                <div style={styles.targetCell}>
                  <span style={styles.cellLabel}>BẢN DỊCH · {segment.sourceSegmentId}</span>
                  <textarea
                    aria-label={`Bản dịch ${segment.sourceSegmentId}`}
                    value={drafts[segment.sourceSegmentId] ?? ''}
                    onChange={(event) =>
                      setDrafts((current) => ({ ...current, [segment.sourceSegmentId]: event.target.value }))
                    }
                    onFocus={() => revealSegment(segment.sourceSegmentId)}
                    onCompositionStart={() => setComposingId(segment.sourceSegmentId)}
                    onCompositionEnd={() => setComposingId(null)}
                    onKeyDown={(event) => {
                      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
                        event.preventDefault();
                        void saveSegment(segment.sourceSegmentId);
                      }
                    }}
                    rows={3}
                    style={styles.textarea}
                  />
                  <div style={styles.actions}>
                    <button
                      type="button"
                      aria-label={`Lưu đoạn ${segment.sourceSegmentId}`}
                      onClick={() => void saveSegment(segment.sourceSegmentId)}
                      disabled={savingId === segment.sourceSegmentId}
                      style={styles.primary}
                    >
                      {savingId === segment.sourceSegmentId ? 'Đang lưu…' : 'Lưu (Ctrl+S)'}
                    </button>
                    {proposal ? (
                      <button
                        type="button"
                        onClick={() =>
                          setDrafts((current) => ({ ...current, [segment.sourceSegmentId]: proposal }))
                        }
                        style={styles.secondary}
                      >
                        Áp dụng đề xuất QA
                      </button>
                    ) : null}
                  </div>
                  {proposal ? <p style={styles.proposal}>Đề xuất: {proposal}</p> : null}
                </div>
              </article>
            );
          })}
        </div>

        <aside aria-label="QA inspector" style={styles.inspector}>
          <h3 style={styles.inspectorTitle}>QA</h3>
          {filteredIssues.length === 0 ? <p style={styles.empty}>Không có vấn đề QA</p> : null}
          {filteredIssues.map((issue) => (
            <button
              key={issue.id}
              type="button"
              aria-pressed={selectedIssueId === issue.id}
              onClick={() => selectIssue(issue)}
              style={{
                ...styles.issue,
                ...(selectedIssueId === issue.id ? styles.issueSelected : {}),
              }}
            >
              <span style={styles.issueTop}>
                {issue.severity} · {issue.category}
              </span>
              <span style={styles.issueText}>{issue.suggestion ?? issue.evidence ?? ''}</span>
              <span style={styles.issueSegment}>Đoạn {issue.sourceSegmentId ?? '—'}</span>
            </button>
          ))}
        </aside>
      </div>

      <ContextInspector chapterId={chapterId} />

      <footer style={styles.footer}>
        <button
          type="button"
          onClick={() => void approve(false)}
          disabled={busy || openCritical.length > 0}
          style={{ ...styles.primary, opacity: openCritical.length > 0 ? 0.6 : 1 }}
        >
          Phê duyệt
        </button>
        {openCritical.length > 0 ? (
          <>
            <span role="status" style={styles.warning}>
              Còn {openCritical.length} lỗi CRITICAL chưa xử lý — cần sửa hoặc bỏ qua có chủ đích.
            </span>
            <button type="button" onClick={() => void approve(true)} disabled={busy} style={styles.danger}>
              Bỏ qua cảnh báo &amp; phê duyệt
            </button>
          </>
        ) : null}
      </footer>
    </section>
  );
}

const styles: Record<string, React.CSSProperties> = {
  shell: { display: 'grid', gap: 12, padding: 16, border: '1px solid #d9e1ea', borderRadius: 8, background: '#ffffff' },
  header: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' },
  title: { margin: 0, fontSize: 20 },
  meta: { margin: '4px 0 0', color: '#667085', fontSize: 13 },
  label: { display: 'grid', gap: 6, fontWeight: 700, fontSize: 13 },
  select: { padding: '8px 10px', border: '1px solid #c8d1dc', borderRadius: 6, background: '#ffffff' },
  layout: { display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 300px', gap: 12, alignItems: 'start' },
  segments: { display: 'grid', gap: 12 },
  row: { display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', gap: 12, padding: 12, border: '1px solid #d9e1ea', borderRadius: 8, background: '#fbfcfe' },
  rowRevealed: { borderColor: '#155eef', boxShadow: '0 0 0 2px #dbe6ff' },
  sourceCell: { display: 'grid', gap: 6, alignContent: 'start' },
  targetCell: { display: 'grid', gap: 8 },
  cellLabel: { fontSize: 11, fontWeight: 800, color: '#667085', textTransform: 'uppercase' },
  sourceText: { margin: 0, lineHeight: 1.6, color: '#344054', whiteSpace: 'pre-wrap' },
  textarea: { width: '100%', boxSizing: 'border-box', padding: 10, border: '1px solid #c8d1dc', borderRadius: 6, font: 'inherit', lineHeight: 1.5, resize: 'vertical' },
  actions: { display: 'flex', gap: 8, flexWrap: 'wrap' },
  primary: { padding: '8px 12px', border: 0, borderRadius: 6, background: '#155eef', color: '#ffffff', fontWeight: 700 },
  secondary: { padding: '8px 12px', border: '1px solid #c8d1dc', borderRadius: 6, background: '#ffffff', fontWeight: 700 },
  danger: { padding: '8px 12px', border: 0, borderRadius: 6, background: '#b45309', color: '#ffffff', fontWeight: 700 },
  proposal: { margin: 0, fontSize: 13, color: '#7a4b00' },
  inspector: { display: 'grid', gap: 8, padding: 10, border: '1px solid #d9e1ea', borderRadius: 8, background: '#f8fafc' },
  inspectorTitle: { margin: 0, fontSize: 14 },
  empty: { margin: 0, color: '#667085', fontSize: 13 },
  issue: { display: 'grid', gap: 4, textAlign: 'left', padding: 8, border: '1px solid #d9e1ea', borderRadius: 6, background: '#ffffff', cursor: 'pointer' },
  issueSelected: { borderColor: '#155eef', background: '#eef4ff' },
  issueTop: { fontSize: 11, fontWeight: 800, color: '#475467' },
  issueText: { fontSize: 13, color: '#344054' },
  issueSegment: { fontSize: 11, color: '#667085' },
  footer: { display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' },
  pick: { display: 'flex', alignItems: 'center', gap: 4 },
  repairBar: { display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' },
  repairHint: { color: '#667085', fontSize: 13 },
  repairRow: { border: '1px solid #e3e8ef', borderRadius: 6, padding: '6px 10px', background: '#fffdf5' },
  repairActions: { display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' },
  repairAccepted: { color: '#166534', fontWeight: 700, fontSize: 13 },
  repairRejected: { color: '#667085', fontSize: 13 },
  success: { margin: 0, color: '#166534', fontWeight: 700 },
  error: { margin: 0, color: '#9a3412', fontWeight: 700 },
  warning: { color: '#92400e', fontWeight: 700, fontSize: 13 },
};
