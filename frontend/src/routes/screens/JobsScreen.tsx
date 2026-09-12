import JobsList from '../../features/jobs/JobsList';
import { JobProgress } from '../../features/jobs/JobProgress';
import { jobActions } from '../jobActions';

import styles from './JobsScreen.module.css';

export function JobsScreen() {
  const { onCancel, onRetry } = jobActions();
  return (
    <section className={styles.panel}>
      <h1 className={styles.title}>Jobs</h1>
      <JobsList onRetry={onRetry} onCancel={onCancel} />
      <JobProgress />
    </section>
  );
}
