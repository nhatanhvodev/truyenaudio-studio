import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { apiJson } from '../../shared/api';

import styles from './BilingualEditor.module.css';

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
  /** Which engine produced the proposal; the route reports the offline one today. */
  generator?: string;
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
    <section aria-label="Editor song ngữ" className={styles.shell}>
      <header className={styles.header}>
        <div>
          <h2 className={styles.title}>Dịch &amp; hiệu đính</h2>
          <p className={styles.meta}>
            Run <span data-testid="run-id">{payload?.run.id}</span> · {payload?.run.status}
          </p>
        </div>
        <label className={styles.label}>
          Lọc QA
          <select
            aria-label="Lọc QA"
            value={severityFilter}
            onChange={(event) => setSeverityFilter(event.target.value)}
            className={styles.select}
          >
            <option value="ALL">ALL</option>
            <option value="CRITICAL">CRITICAL</option>
            <option value="MAJOR">MAJOR</option>
            <option value="MINOR">MINOR</option>
          </select>
        </label>
      </header>

      {message ? <p role="status" className={styles.success}>{message}</p> : null}
      {error ? <p role="alert" className={styles.error}>{error}</p> : null}

      <div className={styles.repairBar}>
        {selectedSegmentIds.length > 0 ? (
          <button
            type="button"
            data-testid="preview-repair"
            onClick={() => void previewRepair()}
            disabled={busy}
            className={styles.secondaryButton}
          >
            Xem đề xuất sửa
          </button>
        ) : null}
        <span className={styles.repairHint}>
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
            className={styles.primaryButton}
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
            generator: repairProposal.generator,
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
                className={styles.repairRow}
              >
                <div className={styles.repairActions}>
                  <strong>{replacement.sourceSegmentId}</strong>
                  {decision === 'accepted' ? (
                    <span className={styles.repairAccepted}>Đã nhận — draft đã đổi</span>
                  ) : decision === 'rejected' ? (
                    <span className={styles.repairRejected}>Đã bỏ qua</span>
                  ) : null}
                  <button
                    type="button"
                    onClick={() => acceptReplacement(replacement)}
                    className={styles.primaryButton}
                  >
                    Chấp nhận
                  </button>
                  <button
                    type="button"
                    onClick={() => rejectReplacement(replacement)}
                    className={styles.secondaryButton}
                  >
                    Bỏ qua
                  </button>
                </div>
              </section>
            );
          })
        : null}

      <div className={styles.layout}>
        <div className={styles.segments}>
          {(payload?.segments ?? []).map((segment, index) => {
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
                className={[styles.segment, revealed ? styles.revealed : ''].filter(Boolean).join(' ')}
              >
                <div className={styles.segmentIndex}>
                  <label className={styles.pick}>
                    <input
                      type="checkbox"
                      aria-label={`Chọn đoạn ${segment.sourceSegmentId}`}
                      checked={selectedSegmentIds.includes(segment.sourceSegmentId)}
                      onChange={() => toggleSegment(segment.sourceSegmentId)}
                    />
                  </label>
                  <span className={styles.ordinal}>{index + 1}</span>
                </div>
                <div className={styles.segmentBody}>
                  <div className={styles.cells}>
                    <div className={styles.cell}>
                      <span className={styles.cellLabel}>GỐC (chỉ đọc)</span>
                      <p
                        aria-readonly="true"
                        data-testid={`source-${segment.sourceSegmentId}`}
                        className={styles.source}
                      >
                        {segment.sourceText}
                      </p>
                    </div>
                    <div className={styles.cell}>
                      <span className={styles.cellLabel}>BẢN DỊCH · {segment.sourceSegmentId}</span>
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
                    className={styles.target}
                  />
                  <div className={styles.actions}>
                    <button
                      type="button"
                      aria-label={`Lưu đoạn ${segment.sourceSegmentId}`}
                      onClick={() => void saveSegment(segment.sourceSegmentId)}
                      disabled={savingId === segment.sourceSegmentId}
                      className={styles.primaryButton}
                    >
                      {savingId === segment.sourceSegmentId ? 'Đang lưu…' : 'Lưu (Ctrl+S)'}
                    </button>
                    {proposal ? (
                      <button
                        type="button"
                        onClick={() =>
                          setDrafts((current) => ({ ...current, [segment.sourceSegmentId]: proposal }))
                        }
                        className={styles.secondaryButton}
                      >
                        Áp dụng đề xuất QA
                      </button>
                    ) : null}
                  </div>
                      {proposal ? <p className={styles.proposal}>Đề xuất: {proposal}</p> : null}
                    </div>
                  </div>
                </div>
              </article>
            );
          })}
        </div>

        <aside aria-label="QA inspector" className={styles.inspector}>
          <h3 className={styles.inspectorTitle}>QA</h3>
          {filteredIssues.length === 0 ? <p className={styles.empty}>Không có vấn đề QA</p> : null}
          {filteredIssues.map((issue) => (
            <button
              key={issue.id}
              type="button"
              aria-pressed={selectedIssueId === issue.id}
              onClick={() => selectIssue(issue)}
              className={[styles.issue, selectedIssueId === issue.id ? styles.issueSelected : '']
                .filter(Boolean)
                .join(' ')}
            >
              <span className={styles.issueTop}>
                <span className={[styles.severity, severityClass(issue.severity)].join(' ')}>{issue.severity}</span> · {issue.category}
              </span>
              <span className={styles.issueText}>{issue.suggestion ?? issue.evidence ?? ''}</span>
              <span className={styles.issueSegment}>Đoạn {issue.sourceSegmentId ?? '—'}</span>
            </button>
          ))}
        </aside>
      </div>

      <ContextInspector chapterId={chapterId} />

      <footer className={styles.footer}>
        <button
          type="button"
          onClick={() => void approve(false)}
          disabled={busy || openCritical.length > 0}
          className={styles.primaryButton}
        >
          Phê duyệt
        </button>
        {openCritical.length > 0 ? (
          <>
            <span role="status" className={styles.warning}>
              Còn {openCritical.length} lỗi CRITICAL chưa xử lý — cần sửa hoặc bỏ qua có chủ đích.
            </span>
            <button type="button" onClick={() => void approve(true)} disabled={busy} className={styles.dangerButton}>
              Bỏ qua cảnh báo &amp; phê duyệt
            </button>
          </>
        ) : null}
      </footer>
    </section>
  );
}

/**
 * Severity is never colour-alone: the word itself is the signal (`.severity*`
 * only tints it). CRITICAL blocks approval, MAJOR is a warning, everything
 * else is advisory.
 */
function severityClass(severity: string): string {
  if (severity === 'CRITICAL') {
    return styles.severityCritical;
  }
  if (severity === 'MAJOR') {
    return styles.severityMajor;
  }
  return styles.severityMinor;
}
