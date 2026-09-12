import { Navigate, useParams } from 'react-router-dom';
import JobDraftPanel from '../../features/jobs/JobDraftPanel';

import styles from './JobDraftScreen.module.css';

export function JobDraftScreen() {  const { jobId } = useParams();
  if (!jobId) {
    return <Navigate to="/jobs" replace />;
  }
  return (
    <section className={styles.panel} aria-label="Nháp job">
      <h1 className={styles.title}>Nháp đang dịch</h1>
      <JobDraftPanel jobId={jobId} />
    </section>
  );
}
