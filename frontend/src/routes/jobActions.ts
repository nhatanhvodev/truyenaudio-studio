import { apiJson } from '../shared/api';
import { defaultJobEventStore, type JobEvent } from '../features/jobs/jobStore';

/**
 * U07: cancel/retry actions shared by the jobs list and the batch queue.
 *
 * Both actions are real API calls; the durable snapshot is re-read afterwards on
 * success *and* failure, so the UI never displays a status the backend did not
 * confirm. A refused action (409/404) is therefore not an error dialog: the job
 * simply shows its real state again.
 */
export function jobActions(store = defaultJobEventStore) {
  const act = (suffix: 'cancel' | 'retry') => async (event: JobEvent) => {
    try {
      await apiJson(`/api/jobs/${encodeURIComponent(event.jobId)}/${suffix}`, { method: 'POST' });
    } catch {
      // Refused (e.g. JOB_RETRY_NOT_RETRYABLE) — refresh() below reconciles.
    }
    await store.refresh();
  };
  return { onCancel: act('cancel'), onRetry: act('retry') };
}
